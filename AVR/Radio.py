import socket
import threading
import json
import sys
from AVR import Utils
import os
import timeit
import zlib

HOST = "localhost" # Accept this from command line
PORT = 5000 # Accept this from the command line
PACKET_SIZE = 60000

RECEIVE_BUFFER_SIZE = 100*PACKET_SIZE
DEBUG = True

class Port():
    def __init__(self, vehicle_id, port_number):
        self.vehicle_id = vehicle_id
        self.port_number = port_number
        self.loss_rate = 0
        self.topics = {}
        self.data_packets_sent = 0
        self.control_packets_sent = 0
    def set_loss_rate(loss_rate):
        self.loss_rate = loss_rate
    def set_connection(self, conn):
        self.conn = conn
    def register_topic(self,topic, callback):
        # check if topic already exists
        self.topics[topic] = callback

class Radio():
    _instance = None
    data_packets_sent = 0
    control_packets_sent = 0
    data_packets_received = 0
    control_packets_received = 0
    def __init__(self):
        if self._instance.__initialized == False:
            self.vehicle_ports = dict() # dictionary to maintain sockets for each vehicle receiving message.
            self.receive_conn = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.receive_conn.settimeout(1)
            self.receive_conn.bind((HOST,PORT))
            self.destroy = False
            self.process = threading.Thread(target=self.receive_msg)
            self.process.start()
            
            if DEBUG:
                print("Radio created. listening on port "+str(PORT))
            self._instance.__initialized = True

    # Radio is a singelton class
    def __new__(cls):
        if not cls._instance:
            cls._instance = object.__new__(cls)
            cls._instance.__initialized = False
            return cls._instance
        return cls._instance

    def _destroy(self):
        self.destroy = True
        self.process.join()
        for k,v in self.vehicle_ports.items():
            self.data_packets_received += v.data_packets_received
            self.control_packets_received += v.control_packets_received

        self.receive_conn.close()
        os.system("tcdel lo --all")
        print("Data packets send: %d data packets received: %d "%(self.data_packets_sent, self.data_packets_received))
        print("Control packets send: %d control packets received: %d "%(self.control_packets_sent, self.control_packets_received))
        with open(Utils.RecordingOutput+str(Utils.EvalEnv.get_trace_id())+"/packet_losses", "w") as f:
            f.write("Data packets send: %d data packets received: %d "%(self.data_packets_sent, self.data_packets_received))
            f.write("Control packets send: %d control packets received: %d "%(self.control_packets_sent, self.control_packets_received))
        if DEBUG:
            print("Radio destroyed")

    def register_topic(self, id, topic, callback):
        self.vehicle_ports[id].register_topic(topic, callback)
        if DEBUG:
            print("vehicle ID: %d topic: %s registered"%(id, topic))

    def add_vehicle(self,vehicle_id):
        # Create a new UDP port and assign it to vehicle
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.bind((HOST, 0))
        self.vehicle_ports[vehicle_id] = Port(vehicle_id, s.getsockname()[1])
        self.vehicle_ports[vehicle_id].set_connection(s)
        if DEBUG:
            print("vehicle ID: %d added port: %d"%(vehicle_id, s.getsockname()[1]))

    def remove_vehicle(self,vehicle_id):
        self.vehicle_ports[vehicle_id].conn.close()
        self.data_packets_sent += self.vehicle_ports[vehicle_id].data_packets_sent
        self.control_packets_sent += self.vehicle_ports[vehicle_id].control_packets_sent
        del self.vehicle_ports[vehicle_id]

    def pub_msg(self, topic, content):
        # Wrap the content in topic so that we can put in in appropriate queue on the receiver side
        
        # Calculate and set the loss rate for the source vehicle
        rate = self.get_loss_rate(content.id)
        if self.vehicle_ports[content.id].loss_rate != rate and rate != 0.0:
            res = os.system("tcset  --overwrite --device lo --network 127.0.0.1 --port %d --loss %.4f"%(self.vehicle_ports[content.id].port_number, rate))
            # Sometimes tcset fails if executed frequently, retry the operation if that happens
            if res != 0:
                res = os.system("tcset  --overwrite --device lo --network 127.0.0.1 --port %d --loss %.4f --debug"%(self.vehicle_ports[content.id].port_number, rate))
                if res != 0:
                    print("Please look at me. I failed myself again! :(")
                else:
                    self.vehicle_ports[content.id].loss_rate = rate
            else:
                self.vehicle_ports[content.id].loss_rate = rate
        
        content = {"topic":topic, "content": content}
        msg = json.dumps(content, default=lambda o: o.__dict__).encode('utf-8')
       
        # The current implementation limits the data list to contain a maximum of 1000 objects to limit the size of data messages
        # Alternative solution is to do compression
        #start = timeit.default_timer()
        # msg = zlib.compress(msg)
        #stop = timeit.default_timer()
        #print("compress: %f"%(stop-start))
        
        for _,v in self.vehicle_ports.items():            
            try:
                v.conn.sendto(msg, (HOST,PORT))
                if topic == "Data":
                    v.data_packets_sent += 1
                else:
                    v.control_packets_sent += 1
            except Exception as e:
                print("topic %s msg size %d"%(topic, len(msg)))
                if topic == "Data":
                    print(len(content["content"].data))
                print(e)
                return e
        '''
        if DEBUG:
            print("pub_msg "+ topic)
        '''
    def get_max_msg_size(self):
        return PACKET_SIZE

    def receive_msg(self):
        while True:
            if self.destroy:
                break
            try:
                msg = self.receive_conn.recvfrom(RECEIVE_BUFFER_SIZE)
            except Exception:
                continue
            '''
            if DEBUG:
                print(msg)
            '''
            #start = timeit.default_timer()
            # msg = zlib.decompress(msg[0]).decode("utf-8")
            #stop = timeit.default_timer()
            #print("decompress: %f"%(stop-start))
            
            json_obj = json.loads(msg[0])
            #json_obj = json.dumps(str(msg[0]))
            #print(json_obj)
  
            if json_obj["topic"] == "Data":
                self.data_packets_received += 1
            else:
                self.control_packets_received += 1
            for k,v in self.vehicle_ports.items():
                port = v
                vehicle = k
                if json_obj["topic"] in port.topics:
                    data = json_obj["content"]
                    content = json.dumps(data, default=lambda o: o.__dict__)
                    port.topics[json_obj["topic"]](content)
    
    def get_vehicle_density(self, ego_id):
        count = 0
        for k in self.vehicle_ports.keys():
            # Get vehicle position from ID
            if Utils.is_vehicle_reachable(ego_id, k):
                count += 1
        return count

    def get_loss_rate(self, vehicle_id):
        nc = 4 # Number of packets on collision, need a better estimate than randomly guessing the value
        CW = 16 # congestion window
        lam = 10 # packet transmission frequency
        Es = 0.001 # average service time
        Ep = 1500 # Average packet size
        Nr = self.get_vehicle_density(vehicle_id)
        Rd= (6/8) * 10**6 # Data rate = 6 Mbps
        row = lam * Es
        X = (1-(1-(row*2/(1+CW)))**(Nr-1))
        Tr = (Ep/Rd) # Tr is the transmission delay; Ignoring the packet header transmission delay
        Y = (Nr -1)*lam*Tr
        pc = X*Y/(1+(X*Y*(nc-1)/(nc)))
        return pc