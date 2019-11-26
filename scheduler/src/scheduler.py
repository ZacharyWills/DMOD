#!/usr/bin/env python3

import sys
import os
import time
import subprocess
import queue
import json, ast
import docker
# from itertools import chain
from pprint import pprint as pp
from redis import Redis, WatchError
import logging
import time

## local imports
from scheduler.utils import keynamehelper as keynamehelper
from scheduler.utils import generate as generate
from scheduler.utils import parsing_nested as pn
from scheduler.src.request import Request
from scheduler.utils.clean import clean_keys

## local imports for unittest
# import scheduler.utils.keynamehelper as keynamehelper
# import scheduler.generate as generate
# import scheduler.parsing_nested as pn
# from scheduler.request import Request
# from scheduler.utils.clean import clean_keys

# client = docker.from_env()
# api_client = docker.APIClient()

MAX_JOBS = 210
Max_Redis_Init = 5

logging.basicConfig(
    filename='scheduler.log',
    level=logging.DEBUG,
    format="%(asctime)s,%(msecs)d %(levelname)s: %(message)s",
    datefmt="%H:%M:%S")

# redis = None

resources = [{'node_id': "Node-0001",
           'Hostname': "***REMOVED***",
           'Availability': "active",
           'State': "ready",
           'CPUs': 18,
           'MemoryBytes': 33548128256
          },
          {'node_id': "Node-0002",
           'Hostname': "***REMOVED***",
           'Availability': "active",
           'State': "ready",
           'CPUs': 96,
           'MemoryBytes': 540483764224
          },
          {'node_id': "Node-0003",
           'Hostname': "***REMOVED***",
           'Availability': "active",
           'State': "ready",
           'CPUs': 96,
           'MemoryBytes': 540483764224
          }
         ]


class Scheduler:
    # def __init__(self, user_id, cpus, mem, resources,
    #              image, constraints, hostname, serv_labels, serv_name,
    #              docker_client=None, api_client=None):
    _jobQ = queue.deque()
    def __init__(self, docker_client=None, api_client=None, redis=None):
        if docker_client:
            self.docker_client = docker_client
            self.api_client = api_client
        else:
            self.checkDocker()
            self.docker_client = docker.from_env()
            self.api_client = docker.APIClient()

        # initialize Redis client
        n = 0
        while (n <= Max_Redis_Init):
            try:
                #self.redis = Redis(host=os.environ.get("REDIS_HOST", "myredis"),
                 self.redis = Redis(host=os.environ.get("REDIS_HOST", "localhost"),
                              port=os.environ.get("REDIS_PORT", 6379),
                              # db=0, encoding="utf-8", decode_responses=True,
                              db=0, decode_responses=True,
                              password='***REMOVED***')
            except:
                logging.debug("redis connection error")
            time.sleep(1)
            n += 1
            if (self.redis != None):
                break

        # self._jobQ = queue.deque()
        # _MAX_JOBS is set to currently available total number of CPUs
        self._MAX_JOBS = MAX_JOBS

    def return42(self):
        return 42

    def create_resources(self):
        """ Create resource from the array of passed resource details"""
        e_set_key = keynamehelper.create_key_name("resources")
        for resource in resources:
            # print("In create_resources: CPUs = ", resource['CPUs'])
            # print("In create_resources: MemoryBytes = ", resource['MemoryBytes'])
            e_key = keynamehelper.create_key_name("resource", resource['node_id'])
            self.redis.hmset(e_key, resource)
            self.redis.sadd(e_set_key, resource['node_id'])

    def create_user_from_username(self, user_id):
        """
           Get user id from the user input, store in database
           More info may be saved in the future
        """
        try:
            c_key = keynamehelper.create_key_name("user", user_id)
            user = {'user_id': user_id}
            self.redis.hmset(c_key, user)
        except:
            logging.debug("user not created")

    def check_single_node_availability(self, user_id, cpus, mem):
        """
        Check available resources to allocate job request to a single node to optimize
        computation efficiency
        """
        if (cpus <= 0):
            logging.debug("Invalid CPUs request: cpus = {}, CPUs should be an integer > 0".format(cpus))
            return

        if (not isinstance(cpus, int)):
            logging.debug("Invalid CPUs request: cpus = {}, CPUs must be a positive integer".format(cpus))
            return

        redis = self.redis

        index = 0
        cpusList = []
        cpus_dict = {}
        for resource in resources:
            NodeId = resource['node_id']
            e_key = keynamehelper.create_key_name("resource", NodeId)
            # if (cpus != 0):
            if (cpus > 0):
                p = redis.pipeline()
                try:
                    redis.watch(e_key)
                    # CPUs = int(redis.hget(e_key, resource['CPUs']))
                    CPUs = int(redis.hget(e_key, "CPUs"))
                    # MemoryBytes = int(redis.hget(e_key, "MemoryBytes"))
                    MemoryBytes = int(redis.hget(e_key, "MemoryBytes"))
                    if (CPUs < cpus):
                        continue
                    else:
                        cpus_alloc = cpus
                        p.hincrby(e_key, "CPUs", -cpus_alloc)
                        p.hincrby(e_key, "MemoryBytes", -mem)
                        req_id = generate.order_id()
                        req_key = keynamehelper.create_key_name("job_request", req_id)
                        req_set_key = keynamehelper.create_key_name("job_request", user_id)
                        user_key = keynamehelper.create_key_name(user_id)
                        Hostname = str(redis.hget(e_key, "Hostname"))
                        cpus_dict = {'req_id': req_id, 'node_id': NodeId, 'Hostname': Hostname, 'cpus_alloc': cpus_alloc,
                                     'mem': mem, 'index': index}
                        p.hmset(req_key, cpus_dict)
                        p.sadd(req_set_key, cpus_dict['req_id'])
                        p.rpush(user_key, req_id)
                        cpusList.append(cpus_dict)
                        p.execute()
                        # index += 1
                        break
                except WatchError:
                    logging.debug("Write Conflict check_single_node_availability: {}".format(e_key))
                finally:
                    p.reset()
                    logging.info("In check_single_node_availability: Allocation complete!")
            else:
                logging.debug("Allocation not performed for NodeId: {}, have {} CPUs, requested {} CPUs".format(NodeId, CPUs, cpus))
        if len(cpusList) == 0:
            logging.info("\nIn check_single_node_availability, allocation not performed: requested {} CPUs too large".format(cpus))
        print("\nIn check_single_node_availability:\ncpusList = {}".format(cpusList))
        return cpusList

    def check_generalized_round_robin(self, user_id, cpus, mem):
        """
        Check available resources on host nodes and allocate in round robin manner even the request
        can fit in a single node. This can be useful in test cases where large number of CPUs is
        inefficient for small domains and in filling the nodes when they are almost full
        """
        if (cpus <= 0):
            logging.debug("Invalid CPUs request: cpus = {},  CPUs should be an integer > 0".format(cpus))
            return

        if (not isinstance(cpus, int)):
            logging.debug("Invalid CPUs request: cpus = {}, CPUs must be a positive integer".format(cpus))
            return

        redis = self.redis
        num_node = len(resources)
        int_cpus = int(cpus / num_node)
        remain_cpus = cpus % num_node

        allocList = []
        iter = 0
        while iter < num_node:
            if (iter < remain_cpus):
                allocList.append(int_cpus+1)
            else:
                allocList.append(int_cpus)
            iter += 1

        # checking there are enough resources
        iter = 0
        # cpusList = []
        for resource in resources:
            NodeId = resource['node_id']
            e_key = keynamehelper.create_key_name("resource", NodeId)
            CPUs = int(redis.hget(e_key, "CPUs"))
            cpus_alloc = allocList[iter]
            if (cpus_alloc > CPUs):
                logging.debug("\nIn check_generalized_round_robin:")
                logging.debug("Requested CPUs greater than CPUs available: requested = {}, available = {}, NodeId = {}".\
                      format(cpus_alloc, CPUs, NodeId))
                # return cpusList
                return
            iter += 1

        index = 0
        cpusList = []
        cpus_dict = {}
        for resource in resources:
            NodeId = resource['node_id']
            e_key = keynamehelper.create_key_name("resource", NodeId)
            # if (cpus != 0):
            if (cpus > 0):
                p = redis.pipeline()
                try:
                    redis.watch(e_key)
                    # CPUs = int(redis.hget(e_key, resource['CPUs']))
                    CPUs = int(redis.hget(e_key, "CPUs"))
                    # MemoryBytes = int(redis.hget(e_key, "MemoryBytes"))
                    MemoryBytes = int(redis.hget(e_key, "MemoryBytes"))
                    cpus_alloc = allocList[index]

                    # if (cpus_alloc != 0):
                    if (cpus_alloc > 0):
                        p.hincrby(e_key, "CPUs", -cpus_alloc)
                        p.hincrby(e_key, "MemoryBytes", -mem)
                        req_id = generate.order_id()
                        req_key = keynamehelper.create_key_name("job_request", req_id)
                        req_set_key = keynamehelper.create_key_name("job_request", user_id)
                        user_key = keynamehelper.create_key_name(user_id)
                        Hostname = str(redis.hget(e_key, "Hostname"))
                        cpus_dict = {'req_id': req_id, 'node_id': NodeId, 'Hostname': Hostname, 'cpus_alloc': cpus_alloc,
                                     'mem': mem, 'index': index}
                        p.hmset(req_key, cpus_dict)
                        p.sadd(req_set_key, cpus_dict['req_id'])
                        p.rpush(user_key, req_id)
                        cpusList.append(cpus_dict)
                        p.execute()
                        index += 1
                except WatchError:
                    logging.debug("Write Conflict check_generalized_round_robin: {}".format(e_key))
                finally:
                    p.reset()
                    logging.info("In check_generalized_round_robin: Allocation complete!")
            else:
                logging.debug("Allocation not performed for NodeId: {}, have {} CPUs, requested {} CPUs".format(NodeId, CPUs, cpus))
        print("\nIn check_generalized_round_robin: \ncpusList:", *cpusList, sep = "\n")
        return cpusList

    def check_availability_and_schedule(self, user_id, cpus, mem):
        """Check available resources on host node and allocate based on user request"""
        if (cpus <= 0):
            logging.debug("Invalid CPUs request: cpus = {}, CPUs should be an integer > 0".format(cpus))
            return

        if (not isinstance(cpus, int)):
            logging.debug("Invalid CPUs request: cpus = {}, CPUs must be a positive integer".format(cpus))
            return

        redis = self.redis
        total_CPUs = 0
        # cpusList = []
        for resource in resources:
            e_key = keynamehelper.create_key_name("resource", resource['node_id'])
            CPUs = int(redis.hget(e_key, "CPUs"))
            total_CPUs += CPUs
        if (cpus > total_CPUs):
            print("\nRequested CPUs greater than CPUs available: requested = {}, available = {}".format(cpus, total_CPUs))
            # return cpusList
            return

        index = 0
        cpusList = []
        cpus_dict = {}
        for resource in resources:
            NodeId = resource['node_id']
            e_key = keynamehelper.create_key_name("resource", NodeId)
            # CPUs = int(redis.hget(e_key, resource['CPUs']))
            CPUs = int(redis.hget(e_key, "CPUs"))

            # if (cpus != 0):
            if (cpus > 0):
                p = redis.pipeline()
                try:
                    redis.watch(e_key)
                    # CPUs = int(redis.hget(e_key, resource['CPUs']))
                    CPUs = int(redis.hget(e_key, "CPUs"))
                    MemoryBytes = int(redis.hget(e_key, "MemoryBytes"))
                    if (CPUs <= cpus):             # request needs one or more nodes
                        cpus -= CPUs               # deduct all CPUs currently available on this node
                        cpus_alloc = CPUs
                    elif (cpus > 0):               # CPUS > cpus, request is smaller than CPUs on this node
                        cpus_alloc = cpus
                        cpus = 0
                    else:
                        break

                    if (cpus_alloc > 0):
                        p.hincrby(e_key, "CPUs", -cpus_alloc)
                        p.hincrby(e_key, "MemoryBytes", -mem)
                        req_id = generate.order_id()
                        # request = {'req_id': req_id, 'user_id': user_id,
                        #            'cpus': cpus, 'mem': mem,
                        #            'resource_node_id': NodeId, 'ts': int(time.time())}
                        req_key = keynamehelper.create_key_name("job_request", req_id)
                        # user_id_num = user_id + job_id
                        req_set_key = keynamehelper.create_key_name("job_request", user_id)
                        user_key = keynamehelper.create_key_name(user_id)
                        # p.hmset(req_key, request)
                        Hostname = str(redis.hget(e_key, "Hostname"))
                        cpus_dict = {'req_id': req_id, 'node_id': NodeId, 'Hostname': Hostname, 'cpus_alloc': cpus_alloc,
                                     'mem': mem, 'index': index}
                        p.hmset(req_key, cpus_dict)
                        p.sadd(req_set_key, cpus_dict['req_id'])
                        p.rpush(user_key, req_id)
                        cpusList.append(cpus_dict)
                        p.execute()
                        index += 1
                except WatchError:
                    logging.debug("Write Conflict check_availability_and_schedule: {}".format(e_key))
                finally:
                    p.reset()
                    logging.info("In check_availability_and_schedule: Allocation complete!")
            else:
                logging.debug("Allocation not performed for NodeId: {}, have {} CPUs, requested {} CPUs".format(NodeId, CPUs, cpus))
        print("\nIn check_availability_and_schedule:\ncpusList: ", *cpusList, sep = "\n")
        return cpusList

    def print_resource_details(self):
        """Print the details of remaining resources after allocating the request """
        logging.info("Resources remaining:")
        for resource in resources:
            e_key = keynamehelper.create_key_name("resource", resource['node_id'])
            logging.info("hgetall(e_key): {}".format(self.redis.hgetall(e_key)))
        logging.info("-" * 20)
        logging.info("\n")

    def service_to_host_mapping(self):
        """find host name based on service info"""
        # This code need split into two

        # docker api
        client = self.docker_client
        api_client = self.api_client

        # test out some service functions
        service_list = client.services.list()
        for service in service_list:
            service_id = service.id
            var = "service:" + service_id

        serviceList = []
        for service in service_list:
            service_id = service.id
            # serv_list = client.services.list(filters={'name':'nwm_mpi-worker'})[0]
            serv_list = client.services.list(filters={'id': service_id})[0]
            service_attrs = serv_list.attrs
            flat_dict = pn.flatten(service_attrs)
            # pp(list(flatten(service_attrs)))
            Name = list(pn.find('Name', service_attrs))[0]
            service_id = serv_list.id
            service_name = serv_list.name
            service_attrs = serv_list.attrs
            flat_dict = pn.flatten(service_attrs)
            Name = list(pn.find('Name', service_attrs))[0]
            if 'nwm_mpi-worker_' not in Name:
                continue
            else:
                Labels = list(pn.find('Labels', service_attrs))[0]
                NameSpace = Labels['com.docker.stack.namespace']
                Hostname = Labels['Hostname']
                cpus_alloc = Labels['cpus_alloc']
                Labels = Labels['com.docker.stack.image']
                (_, Labels) = Labels.split('/')
                Image = list(pn.find('Image', service_attrs))[0]
                # (img_addr, img_name, img_ver, img_id) = (list(pn.find('Image', service_attrs))[0]).split(':')
                (_, HostNode) = ((list(pn.find('Constraints', service_attrs))[0])[0]).split('==')
                # Addr = list(pn.find('Addr', service_attrs))[0]
                # pp(service_attrs)
                service = client.services.get(service_id, insert_defaults=True)
                # task = service.tasks(filters={'name':'nwm_mpi-worker_0'})
                # pp(task)
                # service_dict = {"Name": Name, "Labels": Labels, "HostNode": HostNode, "NameSpace": NameSpace, "img_id": img_id, "Addr": Addr}
                service_dict = {"Name": Name, "Labels": Labels, "HostNode": HostNode, "NameSpace": NameSpace, "Hostname": Hostname, "cpus_alloc": cpus_alloc}
                serviceList.append(service_dict)
                s_key = keynamehelper.create_key_name("service", Name)
                self.redis.hmset(s_key, service_dict)
                logging.info("In service_to_host_mapping: service_dict = {}".format(service_dict))
        logging.info("-" * 50)
        inspect = api_client.inspect_service(service.id, insert_defaults=True)
        print("\nIn In service_to_host_mapping:\nserviceList: ", *serviceList, sep = "\n")
        return serviceList

    def get_node_info(self):
        client = self.docker_client
        api_client = self.api_client

        logging.info("\nnodes info:")
        nodes_list = client.nodes.list()
        nodeList = []
        for node in nodes_list:
            node_id = node.id
            node = client.nodes.get(node_id)
            node_attrs = node.attrs
            ID = list(pn.find('ID', node_attrs))[0]
            Hostname = list(pn.find('Hostname', node_attrs))[0]
            CPUs = int( list(pn.find('NanoCPUs', node_attrs))[0] ) / 1000000000
            MemoryMB = int( list(pn.find('MemoryBytes', node_attrs))[0] ) / 1000000
            State = list(pn.find('State', node_attrs))[0]
            Addr = list(pn.find('Addr', node_attrs))[0]
            node_dict = {"ID": ID, "HostName": Hostname, "CPUs": CPUs, "MemoryMB": MemoryMB, "State": State, "Addr": Addr}
            nodeList.append(node_dict)
            n_key = keynamehelper.create_key_name("Node", Hostname)
            self.redis.hmset(n_key, node_dict)
            logging.info("In get_node_info: node_dict = {}".format(node_dict))
        logging.info("-" * 50)
        print("\nIn get_node_info:\nnodeList: ", *nodeList, sep = "\n")
        return nodeList

    def create_service(self, user_id, image, constraints, hostname, serv_labels, serv_name, mounts, networks, idx, cpusLen, host_str):
        """create new service with Healthcheck, host, and other info"""
        # name = "nwm_mpi-worker"

        # docker api
        client = self.docker_client
        api_client = self.api_client

        Healthcheck = docker.types.Healthcheck(test = ["CMD-SHELL", 'echo Hello'],
                                               interval = 1000000 * 500,
                                               timeout = 1000000 * 6000,
                                               retries = 5,
                                               start_period = 1000000 * 6000)
        if (idx < cpusLen):
            service = client.services.create(image = image,
                                         command = ['sh', '-c', 'sudo /usr/sbin/sshd -D'],
                                         # command = ['sh', '-c', '/nwm/domains/test.sh; sudo /usr/sbin/sshd -D'],
                                         # command = 'sudo /usr/sbin/sshd -D',
                                         # command = 'sleep 60',
                                         constraints = constraints,
                                         hostname = hostname,
                                         labels = serv_labels,
                                         name = serv_name,
                                         mounts = mounts,
                                         networks = networks,
                                         # user = user_id,
                                         healthcheck = Healthcheck)
        else:
            # args = ['nwm_mpi-worker_tmp0:3', 'nwm_mpi-worker_tmp1:3']
            args = host_str
            service = client.services.create(image = image,
                                         # command = ['sh', '-c', 'sudo /usr/sbin/sshd -D'],
                                         command = ['/nwm/run_model.sh'],
                                         args = args,
                                         # command = ['sh', '-c', '/nwm/domains/test.sh; sudo /usr/sbin/sshd -D'],
                                         # command = 'sleep 60',
                                         constraints = constraints,
                                         hostname = hostname,
                                         labels = serv_labels,
                                         name = serv_name,
                                         mounts = mounts,
                                         networks = networks,
                                         # user = user_id,
                                         healthcheck = Healthcheck)

        inspect = api_client.inspect_service(service.id, insert_defaults=True)
        logging.info("Output from inspect_service in create_service():")
        # pp(inspect)
        logging.info("CreatedAt = {}".format(list(pn.find('CreatedAt', inspect))[0]))
        Labels = list(pn.find('Labels', inspect))[0]
        Labels = Labels['com.docker.stack.image']
        (_, Labels) = Labels.split('/')
        (_, HostNode) = ((list(pn.find('Constraints', inspect))[0])[0]).split('==')
        logging.info("HostNode = {}".format(HostNode))
        logging.info("\n")
        # test out some service functions
        serv_list = client.services.list(filters={'name':'nwm_mpi-worker_tmp'})[0]
        service_id = serv_list.id
        logging.info("service_id: {}".format(service_id))
        service_name = serv_list.name
        logging.info("service_name: {}".format(service_name))
        service_attrs = serv_list.attrs
        # pp(service_attrs)
        logging.info("\n")
        return service

    def update_service(self, service, user_id):
        """dynamically change a service based on needs"""
        """create new service with Healthcheck, host, and other info"""
        image = self.image
        constraints = self.constraints
        hostname = self.hostname
        serv_labels = self.serv_labels
        serv_name = self.serv_name

        # docker api
        client = self.docker_client
        api_client = self.api_client

        service.update(image=image,
                        constraints = constraints,
                        hostname = hostname,
                        labels = serv_labels,
                        name = serv_name,
                        mounts = mounts,
                        networks = networks)#,
                        #user = user_id)
        # inspect = api_client.inspect_service(service.id, insert_defaults=True)
        # print("--- output from inspect_service after update ---")
        # pp(inspect)
        print("\n")

        # test out some service functions
        serv_list_tmp = client.services.list(filters={'name':'nwm_mpi-worker_tmp'})
        print("\nservice list:")
        print(serv_list_tmp)
        serv_list = client.services.list(filters={'name':'nwm_mpi-worker_tmp'})[0]
        print("\nservice list")
        print(serv_list)
        print("\nafter updating:")
        service_id = serv_list.id
        print ('service_id: ', service_id)
        service_name = serv_list.name
        print ('service_name: ', service_name)
        service_attrs = serv_list.attrs
        print ("service_attrs:")
        # pp(service_attrs)
        service = client.services.get(service_id, insert_defaults=True)
        task = service.tasks(filters={'name':'nwm_mpi-worker_tmp'})
        print("\ntask:")
        # pp(task)

    def checkDocker(self):
        # Currently only supporting local docker client
        # However, see https://docker-py.readthedocs.io/en/stable/client.html
        # to implement a remote docker server
        try:
            # Check docker client state
            docker.from_env().ping()
        except:
            raise ConnectionError("Please check that the Docker Daemon is installed and running.")

    @classmethod
    def fromRequest(cls, user_id, cpus, mem, idx):
        """Perform job queuing based on Request() class object"""
        # user_id = "shengting.cui"
        # cpus = 125
        # mem = 5000000000
        # resources = []
        # image = ""
        # constraints = []
        # hostname = ""
        # serv_labels = {}
        # serv_name = ""
        # scheduler = cls(user_id, cpus, mem, resources, image, constraints,
        #                 hostname, serv_labels, serv_name)
        # if (idx == 0):
        scheduler = cls()
        request = Request(user_id, cpus, mem)
        scheduler.enqueue(request)
        return scheduler

    def runJob(self, request, image, constraints, hostname, serv_labels, serv_name, cpus_alloc, mounts, networks, idx, cpusLen, host_str):
        user_id = request.user_id
        service = self.create_service(user_id, image, constraints, hostname, serv_labels, serv_name, mounts, networks, idx, cpusLen, host_str)
        # os.system('grep processor /proc/cpuinfo | wc -l')
        return service

    def enqueue(self, request):
        '''
        Add job request to queue
        '''
        self.__class__._jobQ.append(request)
        # self._jobQ.append(request)

    def build_host_list(self, basename, cpusList):
        '''
        build a list of strings that contain the container names and the allocated CPUs on the associated hosts
        '''

        idx = 0
        host_str = []
        # basename = 'nwm_mpi-worker_tmp'
        for cpu in cpusList:
            cpus_alloc = str(cpu['cpus_alloc'])
            name = basename + str(idx)
            host_tmp = name+':'+cpus_alloc
            host_str.append(str(host_tmp))
            idx += 1
        return host_str

    def write_hostfile(self, cpusList):
        '''
        Write allocated hosts and CPUs to hostfile on the scheduler container
        This can be modified to write to a text file for an additional copy of
        the user job info
        '''

        idx = 0
        host_str = ""
        basename = "nwm_mpi-worker_tmp"
        for cpu in cpusList:
            cpus_alloc = str(cpu['cpus_alloc'])
            name = basename + str(idx)
            host_str += name+':'+cpus_alloc+'\n'
            idx += 1

        client = self.docker_client
        service_list = client.services.list()
        for service in service_list:
            service_id = service.id
            serv_list = client.services.list(filters={'id': service_id})[0]
            service_attrs = serv_list.attrs
            Name = list(pn.find('Name', service_attrs))[0]
            # if 'nwm_mpi-worker_tmp0' in Name:
            if 'nwm-_scheduler' in Name:
                with open('hostfile', 'w') as hostfile:
                    hostfile.write(host_str)


    def write_to_hostfile(self):
        """write hostname and cpu allocation to hostfile"""
        # docker api
        client = self.docker_client

        # docker service ls
        host_str = ""
        service_list = client.services.list()
        for service in service_list:
            service_id = service.id
            # serv_list = client.services.list(filters={'name':'nwm_mpi-worker'})[0]
            serv_list = client.services.list(filters={'id': service_id})[0]
            service_attrs = serv_list.attrs
            Name = list(pn.find('Name', service_attrs))[0]
            if 'nwm_mpi-worker_' in Name:
                Labels = list(pn.find('Labels', service_attrs))[0]
                Hostname = Labels['Hostname']
                hostname = Hostname.split('.')[0]
                cpus_alloc = Labels['cpus_alloc']
                # print("In write_to_hostfile: hostname = {}".format(hostname))
                # print("In write_to_hostfile: cpus_alloc = {}".format(cpus_alloc))
                host_str += Name+':'+cpus_alloc+'\n'

        for service in service_list:
            service_id = service.id
            serv_list = client.services.list(filters={'id': service_id})[0]
            service_attrs = serv_list.attrs
            Name = list(pn.find('Name', service_attrs))[0]
            # if 'nwm_mpi-worker_tmp0' in Name:
            if 'nwm_mpi-worker_' in Name:
                with open('hostfile', 'w') as hostfile:
                    hostfile.write(host_str)

    def retrieve_job_metadata(self, user_id):
        """
        Retrieve queued job info from the database using user_id as a key to the req_id list
        Using req_id to uniquely retrieve the job request dictionary: cpus_dict
        Build nested cpusList from cpus_dict
        The code only retrieve one job that make up cpusList. Complete job list is handled in check_jobQ
        For comprehensive info on all jobs by a user in the database, a loop can be used to call this method
        """

        redis = self.redis
        cpusList = []
        user_key = keynamehelper.create_key_name(user_id)

        # case for index = 0, the first popped index is necessarily 0
        # lpop and rpush are used to guaranttee that the earlist queued job gets to run first
        req_id = redis.lpop(user_key)
        if (req_id != None):
            print("In retrieve_job_metadata: user_key", user_key, "req_id = ", req_id)
            req_key = keynamehelper.create_key_name("job_request", req_id)
            cpus_dict = redis.hgetall(req_key)
            cpusList.append(cpus_dict)
            index = cpus_dict['index']             # index = 0
            if (int(index) != 0):
                raise Exception("Metadata access error, index = ", index, " req_id = ", req_id)

        '''
        # case for index = 0 or 1, job belongs to a different request if index = 0
        req_id = redis.lpop(user_key)
        if (req_id != None):
            print("In retrieve_job_metadata: user_key", user_key, "req_id = ", req_id)
            req_key = keynamehelper.create_key_name("job_request", req_id)
            cpus_dict = redis.hgetall(req_key)
            index = cpus_dict['index']             # index = 0 or 1
            if (str(index) == '0'):
                redis.lpush(user_key, req_id)      # return the popped value, the job request belongs to a different request if index = 0
            else:
                cpusList.append(cpus_dict)
        '''

        # cases for the rest of index != 0, job belongs to a different request if index = 0
        while (req_id != None):                    # previous req_id
            req_id = redis.lpop(user_key)          # new req_id
            if (req_id != None):
                req_key = keynamehelper.create_key_name("job_request", req_id)
                cpus_dict = redis.hgetall(req_key)
                index = cpus_dict['index']         # new index
                if (int(index) == 0):
                    redis.lpush(user_key, req_id)  # return the popped value, the job request belongs to a different request if index = 0
                    break
                else:
                    cpusList.append(cpus_dict)
                print("In retrieve_job_metadata: user_key", user_key, "req_id = ", req_id)
        print("\nIn retrieve_job_metadata: cpusList:\n", *cpusList, sep = "\n")
        print("\nIn retrieve_job_metadata:")
        print("\n")
        return cpusList


    def startJobs(self, user_id, cpus, mem, image, constraints, hostname, serv_labels, serv_name, cpus_alloc, mounts, networks, idx, cpusLen, host_str):
        """
        Using the set max jobs and max cpus spawn docker containers
        until the queue has been exhausted.
        """
        client = self.docker_client
        # Check if number of running jobs is greater than allowed
        if len(client.services.list()) > self._MAX_JOBS:
            raise Exception('System already has too many running containers. '
                            'Either kill containers or adjust the max_jobs '
                            'attribute.')
        # que = self._jobQ
        # for q in que:
            # print("In startJobs, _jobQ: user_id, cpus, mem: {} {} {}".format(q.user_id, q.cpus, q.mem))

        while len(self._jobQ) != 0:
        # if len(self._jobQ) != 0:
            # if len(self.check_availability_and_schedule()) != 0:
            req = self._jobQ.popleft()
            service = self.runJob(req, image, constraints, hostname, serv_labels, serv_name, cpus_alloc, mounts, networks, idx, cpusLen, host_str)
            # running_services_list = client.services.list()

    def check_jobQ(self):
        """ Check jobs in the waiting queue """
        print("In check_jobQ, length of jobQ:", len(self._jobQ))
        que = self._jobQ
        # print("In check_jobQ, que = ", que)
        for job in que:
            print("In check_jobQ: user_id, cpus, mem: {} {} {}".format(job.user_id, job.cpus, job.mem))

    def check_runningJobs(self):
        """
        Check the running job queue
        Running job snapshot is needed for restart
        """
        # docker api
        client = self.docker_client
        api_client = self.api_client

        # test out some service functions
        service_list = client.services.list()
        runningJobList = []
        for service in service_list:
            # iterate through entire service list
            service_id = service.id
            # print("In check_runningJobs: service_id = {}".format(service_id))
            # serv_list = client.services.list(filters={'id': service_id})[0]
            # service_attrs = serv_list.attrs
            service_attrs = service.attrs
            flat_dict = pn.flatten(service_attrs)
            Name = list(pn.find('Name', service_attrs))[0]
            # print("In check_runningJobs: Name = {}".format(Name))
            # service_id = serv_list.id
            # print("In check_runningJobs: service_id = {}".format(service_id))
            # service_name = serv_list.name
            service_name = service.name
            # print("In check_runningJobs: service_name = {}".format(service_name))
            # service_attrs = serv_list.attrs
            # flat_dict = pn.flatten(service_attrs)
            # Name = list(pn.find('Name', service_attrs))[0]
            # Name should be in the form: nwm_mpi-worker_user-id_job-id
            # Select only the service with "nwm-mpi-worker_" in the service name
            if 'nwm_mpi-worker_tmp' in Name:
                Labels = list(pn.find('Labels', service_attrs))[0]
                NameSpace = Labels['com.docker.stack.namespace']
                Hostname = Labels['Hostname']
                cpus_alloc = Labels['cpus_alloc']
                print("In check_runningJobs: Hostname = {}".format(Hostname))
                print("In check_runningJobs: cpus_alloc = {}".format(cpus_alloc))
                Labels = Labels['com.docker.stack.image']
                (_, Labels) = Labels.split('/')
                print("In check_runningJobs: Labels = {}".format(Labels))
                (_, HostNode) = ((list(pn.find('Constraints', service_attrs))[0])[0]).split('==')
                print("In check_runningJobs: HostNode = {}".format(HostNode))
                service = client.services.get(service_id, insert_defaults=True)
                service_dict = {"Name": Name, "Labels": Labels, "HostNode": HostNode, "NameSpace": NameSpace, "Hostname": Hostname, "cpus_alloc": cpus_alloc}
                runningJobList.append(service_dict)
                s_key = keynamehelper.create_key_name("service", Name)
                self.redis.hmset(s_key, service_dict)
                print("-" * 30)
                print("\n")
        print("-" * 50)
        logging.info("\n")
        return runningJobList


    def clean_redisKeys(self):
        '''
        """ initialize Redis client """
        # from utils.clean import clean_keys

        global redis
        n = 0
        while (n <= Max_Redis_Init):
            try:
                redis = Redis(host=os.environ.get("REDIS_HOST", "myredis"),
                # redis = Redis(host=os.environ.get("REDIS_HOST", "localhost"),
                              port=os.environ.get("REDIS_PORT", 6379),
                              db=0, decode_responses=True,
                              password='***REMOVED***')
            except:
                logging.debug("redis connection error")
            time.sleep(1)
            n += 1
            if (redis != None):
                break

        # time.sleep(5)
        '''
        clean_keys(self.redis)
        # self.redis.flushdb()
        # self.redis.flushall()

def check_for_incoming_req():
    '''
    Place holder for codes checking incoming job request
    '''
    time.sleep(5)
    recvJobReq = 1
    return recvJobReq

def test_scheduler():
    """Test the scheduler using on the fly cpusList and the metadata from the saved database"""
    # user_id = "shengting.cui"
    # cpus = 24
    # mem = 5000000000

    # instantiate the scheduler
    scheduler = Scheduler()

    # initialize redis client
    scheduler.clean_redisKeys()

    # build resource database
    scheduler.create_resources()

    # find host from docker service info
    # this may eventually become part of job queue monitoring tool
    # scheduler.service_to_host_mapping()

    # parsing job request
    # schedule = scheduler.fromRequest(user_id, cpus, mem)

    recvJobReq = 0
    while (recvJobReq != 1):
        recvJobReq = check_for_incoming_req()
        if (recvJobReq):
            user_id = "shengting.cui"
            cpus = 10
            mem = 5000000000
            # create and save user info to database
            scheduler.create_user_from_username(user_id)

            """
            check_availability_and_schedule() returns cpusList which contains CPU allocation on one or multiple nodes
            based on user request
            It also saves the cpusList to the database as well as req_id as a key for finding the job request
            for later use

            check_single_node_availability() find the first node with enough CPUs to accomodate a job request, loading a
            job request to a single node optimize the computation efficiency

            check_generalized_round_robin() distributes a compute job among a set of nodes, even though the job can fit in
            a single node. This is useful in some special cases
            """

            # First try schedule the job on a single node. If for some reason, job cannot be allocated on a single node,
            # an empty list is returned, we try the check_generalized_round_robin() method. If this is not successful,
            # we try the more general check_availability_and_schedule() method
            '''
            if (cpus <= 96):
                # cpus = 16
                cpusList = scheduler.check_single_node_availability(user_id, cpus, mem)

            if (len(cpusList) == 0):
                # cpus = 10
                cpusList = scheduler.check_generalized_round_robin(user_id, cpus, mem)

            if (len(cpusList) == 0):
                # cpus = 140
                cpusList = scheduler.check_availability_and_schedule(user_id, cpus, mem)
            '''
            # cpus = 11
            # cpusList = scheduler.check_generalized_round_robin(user_id, cpus, mem)
            cpus = 8
            cpusList = scheduler.check_single_node_availability(user_id, cpus, mem)

            if (len(cpusList) == 0):
                print("Illegitimate request not scheduled")
                return

            # scheduler.write_hostfile(cpusList)
            print("\nIn test_scheduler, cpusList: ", cpusList)
            print("\n")
            cpusList = scheduler.retrieve_job_metadata(user_id)
            print("\nIn test_scheduler: cpusList:\n", *cpusList, sep = "\n")
            scheduler.print_resource_details()

            '''
            idx = 0
            host_str = []
            basename = 'nwm_mpi-worker_tmp'
            for cpu in cpusList:
                cpus_alloc = str(cpu['cpus_alloc'])
                name = basename + str(idx)
                host_tmp = name+':'+cpus_alloc
                # host_str_tmp = str(host_tmp)
                # print("host_str_tmp = ", host_str_tmp)
                host_str.append(str(host_tmp))
                idx += 1
            '''
            basename = 'nwm_mpi-worker_tmp'
            host_str = scheduler.build_host_list(basename, cpusList)

            # initialize variables for create_service()
            image = "127.0.0.1:5000/nwm-2.0:latest"
            constraints = []
            # hostname = "{{.Service.Name}}-{{.Task.Slot}}"
            hostname = "{{.Service.Name}}"
            labels =  {"com.docker.stack.image": "127.0.0.1:5000/nwm-2.0",
                       "com.docker.stack.namespace": "nwm"
                      }
            name = "nwm_mpi-worker_tmp"
            # networks = ["mpi-net", "back40"]
            networks = ["mpi-net"]

            idx = 0
            cpusLen = len(cpusList)
            for cpu in cpusList:
                name = "nwm_mpi-worker_tmp"
                constraints = "node.hostname == "
                NodeId = cpu['node_id']
                if (NodeId == "Node-0001"):
                    mounts = ['/opt/nwm_c/domains:/nwm/domains:rw']
                else:
                    mounts = ['/local:/nwm/domains:rw']
                cpus_alloc = str(cpu['cpus_alloc'])
                # print("In test_scheduler, cpus_alloc = {}".format(cpus_alloc))
                Hostname = cpu['Hostname']
                logging.info("Hostname: {}".format(Hostname))
                labels_tmp = {"Hostname": Hostname, "cpus_alloc": cpus_alloc}
                labels.update(labels_tmp)
                constraints += Hostname
                constraints = list(constraints.split("/"))
                name += str(idx)
                idx += 1
                schedule = scheduler.fromRequest(user_id, cpus_alloc, mem, idx)
                # schedule.check_jobQ()
                schedule.startJobs(user_id, cpus, mem, image, constraints, hostname, labels, name, cpus_alloc, mounts, networks, idx, cpusLen, host_str)
            logging.info("\n")
            schedule.check_jobQ()
            jobQ = scheduler._jobQ
            for job in jobQ:
                logging.info("In test_scheduler: user_id, cpus, mem: {} {} {}".format(job.user_id, job.cpus, job.mem))
            scheduler.service_to_host_mapping()
            runningJobs = scheduler.check_runningJobs()
            # scheduler.write_to_hostfile()
            # scheduler.update_service(service)
            recvJobReq = 0
            break    # This is for testing, should not be needed in the final version

    '''
    user_id = "shengting.cui"
    cpus = 125
    mem = 5000000000
    scheduler.create_user_from_username(user_id)
    cpusList = scheduler.check_availability_and_schedule(user_id, cpus, mem)
    print("\nIn test_scheduler, cpusList: ", cpusList)
    print("\n")
    cpusList = scheduler.retrieve_job_metadata(user_id)
    scheduler.print_resource_details()
    # scheduler.service_to_host_mapping()

    # initialize variables for create_service()
    image = "127.0.0.1:5000/nwm-2.0:latest"
    constraints = []
    # hostname = "{{.Service.Name}}-{{.Task.Slot}}"
    hostname = "{{.Service.Name}}"
    labels =  {"com.docker.stack.image": "127.0.0.1:5000/nwm-2.0",
               "com.docker.stack.namespace": "nwm"
              }
    name = "nwm_mpi-worker_tmp"
    # networks = ["mpi-net", "back40"]
    networks = ["mpi-net"]
    idx = 0
    for cpu in cpusList:
        name = "nwm_mpi-worker_"
        constraints = "node.hostname == "
        NodeId = cpu['node_id']
        if (NodeId == "Node-0001"):
            mounts = ['/opt/nwm_c/domains:/nwm/domains:rw']
        else:
            mounts = ['/local:/nwm/domains:rw']
        cpus_alloc = str(cpu['cpus_alloc'])
        # print("In test_scheduler, cpus_alloc = {}".format(cpus_alloc))
        Hostname = cpu['Hostname']
        logging.info("Hostname: {}".format(Hostname))
        labels_tmp = {"Hostname": Hostname, "cpus_alloc": cpus_alloc}
        labels.update(labels_tmp)
        logging.info("labels: {}".format(labels))
        constraints += Hostname
        constraints = list(constraints.split("/"))
        logging.info("constraints: {}".format(constraints))
        name += str(idx)
        idx += 1
        schedule = scheduler.fromRequest(user_id, cpus_alloc, mem, idx)
        schedule.startJobs(user_id, cpus, mem, image, constraints, hostname, labels, name, cpus_alloc, mounts, networks, idx, cpusLen, host_str)
    print("\n")
    scheduler.service_to_host_mapping()
    # scheduler.write_to_hostfile()
    '''


if __name__ == "__main__":
    keynamehelper.set_prefix("stack0")
    # while True:     # Using this while loop causes a name nwm_mpi-worker_tmp0 exists error when looping through 2nd time
    test_scheduler()  # to run test_scheduler(). The while loop does work as expected.
    # while True:
    #     pass