#!/usr/bin/env python3
"""
This script populated Cachet of Zabbix6 IT Services
"""
import sys
import os
import datetime
import json
import requests
import time
import threading
import logging
import yaml
import pytz
from pyzabbix import ZabbixAPI, ZabbixAPIException
from operator import itemgetter

__author__ = 'Artem Alexandrov <qk4l()tem4uk.ru>'
__license__ = """The MIT License (MIT)"""
__version__ = '1.3.8'


def client_http_error(url, code, message):
    logging.error('ClientHttpError[%s, %s: %s]' % (url, code, message))


def cachetapiexception(message):
    logging.error(message)


def pyzabbix_safe(fail_result=False):

    def wrap(func):
        def wrapperd_f(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except (requests.ConnectionError, ZabbixAPIException) as e:
                logging.error('Zabbix Error: {}'.format(e))
                return fail_result
        return wrapperd_f
    return wrap


class Zabbix:
    def __init__(self, server, user, password, verify=True):
        """
        Init zabbix class for further needs
        :param user: string
        :param password: string
        :return: pyzabbix object
        """
        self.server = server
        self.user = user
        self.password = password
        # Enable HTTP auth
        s = requests.Session()
        s.auth = (user, password)

        self.zapi = ZabbixAPI(server, s)
        self.zapi.session.verify = verify
        self.zapi.login(user, password)
        self.version = self.get_version()

    @pyzabbix_safe()
    def get_version(self):
        """
        Get Zabbix API version
        :return: str
        """
        version = self.zapi.apiinfo.version()
        return version

    @pyzabbix_safe({})
    def get_status(self, serviceid):
        """
        Get status of a service
        @param serviceid: string
        @return: dict of data
        """
        service = self.zapi.service.get(
            serviceids=serviceid
            )
        return int(service[0]['status'])

    @pyzabbix_safe({})
    def has_childs(self, serviceid):
        """
        Return if service has childs
        @param serviceid: string
        @return: dict of data
        """
        # Fet per ZBX6 
        service = self.zapi.service.get(
            serviceids=serviceid,
            selectChildren='extend'
            )
        return bool(len(service[0]['children']))

    @pyzabbix_safe({})
    def get_service_name(self, serviceid):
        """
        Get name of a service
        @param serviceid: string
        @return: dict of data
        """
        service = self.zapi.service.get(
            serviceids=serviceid
            )
        return str(service[0]['name'])

    @pyzabbix_safe([])
    def get_itservices(self, root=None):
        """
        Return tree of Zabbix IT Services
        root (hidden)
           - service1 (Cachet componentgroup)
             - child_service1 (Cachet component)
             - child_service2 (Cachet component)
           - service2 (Cachet componentgroup)
             - child_service3 (Cachet component)
        :param root: Name of service that will be root of tree.
                    Actually it will not be present in return tree.
                    It's using just as a start point , string
        :return: Tree of Zabbix IT Services
        :rtype: list
        """
        if root:
            root_service = self.zapi.service.get(
                selectChildren='extend',
                selectTags='extend',
                filter={'name': root})
            try:
                root_service = root_service[0]
            except IndexError:
                logging.error('Can not find "{}" service in Zabbix'.format(root))
                sys.exit(1)
            service_ids = []
            for child in root_service['children']: 
                service_ids.append(child['serviceid'])
            services = self.zapi.service.get(
                selectChildren='extend',
                selectTags='extend',
                serviceids=service_ids)
        else:
            services = self.zapi.service.get(
                selectChildren='extend',
                selectTags='extend',
                output='extend')
        if not services:
            logging.error('Can not find any child service for "{}"'.format(root))
            return []
        # Create a tree of services
        known_ids = []
        # At first proceed services with dependencies as groups
        service_tree = [i for i in services if i['children']]
        for idx, service in enumerate(service_tree):
            child_services_ids = []
            for child in service['children']: 
                child_services_ids.append(child['serviceid'])
            child_services = self.zapi.service.get(
                    selectChildren='extend',
                    selectTags='extend', 
                    serviceids=child_services_ids)
            service_tree[idx]['children'] = child_services
            # Save ids to filter them later
            known_ids = known_ids + child_services_ids
            known_ids.append(service['serviceid'])
        # At proceed services without dependencies as singers
        singers_services = [i for i in services if i['serviceid'] not in known_ids]
        if singers_services:
            service_tree = service_tree + singers_services
        return service_tree


class Cachet:
    def __init__(self, server, token, verify=True):
        """
        Init Cachet class for further needs
        : param server: string
        :param token: string
        :return: object
        """
        self.server = server + '/api/v1/'
        self.token = token
        self.headers = {'X-Cachet-Token': self.token, 'Accept': 'application/json; indent=4'}
        self.verify = verify
        self.version = self.get_version()

    def _http_post(self, url, params):
        """
        Make POST and return json response
        :param url: str
        :param params: dict
        :return: json
        """
        url = self.server + url
        logging.debug("Sending to {url}: {param}".format(url=url,
                                                         param=json.dumps(params,
                                                                          indent=4,
                                                                          separators=(',', ': '))))
        try:
            r = requests.post(url=url, data=params, headers=self.headers, verify=self.verify)
        except requests.exceptions.RequestException as e:
            raise client_http_error(url, None, e)
        # r.raise_for_status()
        if r.status_code != 200:
            return client_http_error(url, r.status_code, r.text)
        try:
            r_json = json.loads(r.text)
        except ValueError:
            raise cachetapiexception(
                "Unable to parse json: %s" % r.text
            )
        logging.debug("Response Body: %s", json.dumps(r_json,
                                                      indent=4,
                                                      separators=(',', ': ')))
        return r_json

    def _http_get(self, url, params=None):
        """
        Helper for HTTP GET request
        :param: url: str
        :param: params:
        :return: json data
        """
        if params is None:
            params = {}
        url = self.server + url
        logging.debug("Sending to {url}: {param}".format(url=url,
                                                         param=json.dumps(params,
                                                                          indent=4,
                                                                          separators=(',', ': '))))
        try:
            r = requests.get(url=url, headers=self.headers, params=params, verify=self.verify)
        except requests.exceptions.RequestException as e:
            raise client_http_error(url, None, e)
        # r.raise_for_status()
        if r.status_code != 200:
            return client_http_error(url, r.status_code, json.loads(r.text)['errors'])
        try:
            r_json = json.loads(r.text)
        except ValueError:
            raise cachetapiexception(
                "Unable to parse json: %s" % r.text
            )
        logging.debug("Response Body: %s", json.dumps(r_json,
                                                      indent=4,
                                                      separators=(',', ': ')))
        return r_json

    def _http_put(self, url, params):
        """
        Make PUT and return json response
        :param url: str
        :param params: dict
        :return: json
        """
        url = self.server + url
        logging.debug("Sending to {url}: {param}".format(url=url,
                                                         param=json.dumps(params,
                                                                          indent=4,
                                                                          separators=(',', ': '))))
        try:
            r = requests.put(url=url, json=params, headers=self.headers, verify=self.verify)
        except requests.exceptions.RequestException as e:
            raise client_http_error(url, None, e)
        # r.raise_for_status()
        if r.status_code != 200:
            return client_http_error(url, r.status_code, r.text)
        try:
            r_json = json.loads(r.text)
        except ValueError:
            raise cachetapiexception(
                "Unable to parse json: %s" % r.text
            )
        logging.debug("Response Body: %s", json.dumps(r_json,
                                                      indent=4,
                                                      separators=(',', ': ')))
        return r_json

    def get_version(self):
        """
        Get Cachet version for logging
        :return: str
        """
        url = 'version'
        data = self._http_get(url)
        return data['data']

    def get_component(self, id):
        """
        Get component params based its id
        @param id: string
        @return: dict
        """
        url = 'components/' + str(id)
        data = self._http_get(url)
        return data

    def get_components(self, name=None):
        """
        Get all registered components or return a component details if name specified
        Please note, it name was not defined method returns only last page of data
        :param name: Name of component to search
        :type name: str
        :return: Data =)
        :rtype: dict or list
        """
        url = 'components'
        data = self._http_get(url)
        total_pages = int(data['meta']['pagination']['total_pages'])
        if name:
            components = []
            for page in range(total_pages, 0, -1):
                if page == 1:
                    data_page = data
                else:
                    data_page = self._http_get(url, params={'page': page})
                for component in data_page['data']:
                    if component['name'] == name:
                        components.append(component)
            if len(components) < 1:
                return {'id': 0, 'name': 'Does not exists'}
            else:
                return components
        return data

    def new_components(self, name, **kwargs):
        """
        Create new components
        @param name: string
        @param kwargs: various additional values =)
        @return: dict of data
        """
        # Get values for new component
        params = {'name': name, 'link': '', 'description': '', 'status': '1', 'group_id': 0, 'tags':''}
        params.update(kwargs)
        # Do not post empty params to Cachet
        for i in ('link', 'description'):
            # Strip params to avoid empty (' ') values #24
            if str(params[i]).strip() == '':
                params.pop(i)
        # Check if components with same name already exists in same group
        component = self.get_components(name)
        # There are more that one component with same name already
        if isinstance(component, list):
            for i in component:
                if i['group_id'] == params['group_id']:
                    return i
        elif isinstance(component, dict):
            if not component['id'] == 0 and component.get('group_id', None) == params['group_id']:
                return component
        # Create component if it does not exist or exist in other group
        url = 'components'
        # params = {'name': name, 'link': link, 'description': description, 'status': status}
        logging.debug('Creating Cachet component {name}...'.format(name=params['name']))
        data = self._http_post(url, params)
        logging.info('Component {name} was created in group id {group_id}.'.format(name=params['name'],
                                                                                   group_id=data['data'][
                                                                                       'group_id']))
        return data['data']

    def upd_components(self, id, **kwargs):
        """
        Update component
        @param id: string
        @param kwargs: various additional values =)
        @return: boolean
        """
        url = 'components/' + str(id)
        params = self.get_component(id)['data']
        if isinstance(params['tags'], dict):
            # Agafem les claus del diccionari en una llista amb la funció keys()
            my_list = list(params['tags'].keys())

            # Ordenem la llista alfabèticament amb la funció sort()
            my_list.sort()

            # Creem un string amb la llista ordenada utilitzant la funció join()
            params['tags'] = ",".join(my_list)

        params.update(kwargs)
        data = self._http_put(url, params)
        if data:
            logging.info('Component {name} (id={id}) was updated. Status - {status}'.format(
                name=data['data']['name'],
                id=id,
                status=data['data']['status_name']))
        return data

    def get_components_gr(self, name=None):
        """
        Get all registered components group or return a component group details if name specified
        Please note, it name was not defined method returns only last page of data
        @param name: string
        @return: dict of data
        """
        url = 'components/groups'
        data = self._http_get(url)
        total_pages = int(data['meta']['pagination']['total_pages'])
        if name:
            for page in range(total_pages, 0, -1):
                if page == 1:
                    data_page = data
                else:
                    data_page = self._http_get(url, params={'page': page})
                for group in data_page['data']:
                    if group['name'] == name:
                        return group
            return {'id': 0, 'name': 'Does not exists'}
        return data

    def new_components_gr(self, name):
        """
        Create new components group
        @param name: string
        @return: dict of data
        """
        # Check if component's group already exists
        components_gr_id = self.get_components_gr(name)
        if components_gr_id['id'] == 0:
            url = 'components/groups'
            # TODO: make if possible to configure default collapsed value
            params = {'name': name, 'collapsed': 2}
            logging.debug('Creating Component Group {}...'.format(params['name']))
            data = self._http_post(url, params)
            if 'data' in data:
                logging.info('Component Group {} was created ({})'.format(params['name'], data['data']['id']))
            return data['data']
        else:
            return components_gr_id

    def get_incident(self, component_id):
        """
        Get last incident for component_id
        @param component_id: string
        @return: dict of data
        """
        # TODO: make search by name
        url = 'incidents'
        data = self._http_get(url)
        total_pages = int(data['meta']['pagination']['total_pages'])
        for page in range(total_pages, 0, -1):
            data = self._http_get(url, params={'page': page})
            data_sorted = sorted(data['data'], key=itemgetter('id'), reverse=True)
            for incident in data_sorted:
                if str(incident['component_id']) == str(component_id):
                    # Convert status to str
                    incident['status'] = str(incident['status'])
                    return incident
        return {'id': '0', 'name': 'Does not exist', 'status': '-1'}

    def new_incidents(self, **kwargs):
        """
        Create a new incident.
        @param kwargs: various additional values =)
                        name, message, status,
                        component_id, component_status
        @return: dict of data
        """
        params = {'visible': 1, 'notify': 'true'}
        url = 'incidents'
        params.update(kwargs)
        data = self._http_post(url, params)
        logging.info('Incident {name} (id={incident_id}) was created for component id {component_id}.'.format(
            name=params['name'],
            incident_id=data['data']['id'],
            component_id=params['component_id']))
        return data['data']

    def upd_incident(self, id, **kwargs):
        """
        Update incident
        @param id: string
        @param kwargs: various additional values =)
                message, status,
                component_status
        @return: boolean
        """
        url = 'incidents/' + str(id)
        params = kwargs
        data = self._http_put(url, params)
        logging.info('Incident ID {id} was updated. Status - {status}.'.format(id=id,
                                                                               status=data['data']['human_status']))
        return data


def services_watcher(service_map):
    """
    Check zabbix services and update Cachet components
    Zabbix Status:
        -1 - OK;
        0 - Not Classified;
        1 - information;
        2 - Warning
        3 - average;
        4 - high;
        5 - disaster.
    Cachet Statuses:
        1 - Operational
        2 - Performance issues
        3 - Partial Outage
        4 - Major Outage
    @param service_map: list of tuples
    @return: boolean
    """
    for i in service_map:
        inc_msg = ''

        if 'serviceid' in i:
            service_state = zapi.get_status(i['serviceid'])
            
            # by default, the service is on operational mode
            new_component_state = 1
            old_component_state = int(cachet.get_component(i['component_id'])['data']['status'])

            # incident cases:
            if service_state == -1:
                # Operational. 
                new_component_state = 1
            elif service_state == 0:
                # No info. 
                new_component_state = 2
            elif service_state == 1:
                # Information issue
                new_component_state = 2
            elif service_state == 2:
                # Warning issue
                new_component_state = 2
            elif service_state == 3:
                # Average issue
                new_component_state = 2
            elif service_state == 4:
                # High issue
                new_component_state = 3
            else:
                # Disaster issue
                new_component_state = 4

            cachet.upd_components(i['component_id'], status=new_component_state)  
            
            # do not register incidents for components groups (services which have childrens)
            if zapi.has_childs(i['serviceid']):
                continue
            # check if they were some changes on the state
            if new_component_state != old_component_state:
                if (new_component_state > 1 and old_component_state == 1):
                    inc_name = zapi.get_service_name(i['serviceid'])
                    inc_msg = ""
                    if servicedown_tmpl:
                        inc_msg = servicedown_tmpl.format(sname=inc_name)
                    else:
                        inc_msg = "Service " + inc_name + " is currently unavailable."
                    inc_status = "1" # status: investigating

                    cachet.new_incidents(name=inc_name, message=inc_msg, status=inc_status,
                                         component_id=i['component_id'], component_status=new_component_state)

                elif (new_component_state == 1 and old_component_state > 1):
                    last_inc = cachet.get_incident(i['component_id'])
                    if last_inc['id']!='0':
                        if resolving_tmpl:
                            inc_msg = resolving_tmpl.format(time=datetime.datetime.now(tz=tz).strftime('%b %d, %H:%M'),
                                            ) + cachet.get_incident(i['component_id'])['message']
                        else:
                            inc_msg = cachet.get_incident(i['component_id'])['message']
                        cachet.upd_incident(last_inc['id'],
                                        status=4,
                                        component_id=i['component_id'],
                                        component_status=1,
                                        message=inc_msg)

            # check if alarmed but there are incidents registered
            elif (new_component_state > 1):
                last_inc = cachet.get_incident(i['component_id'])
                # if id==0, then the incident does not exist
                # if status==4, then the last incident was fixed, but now there's an incident
                if last_inc['id']=='0' or last_inc['status']=='4':
                    inc_name = zapi.get_service_name(i['serviceid'])
                    inc_msg = ""
                    if servicedown_tmpl:
                        inc_msg = servicedown_tmpl.format(sname=inc_name)
                    else:
                        inc_msg = "Service " + inc_name + " is currently unavailable."
                    inc_status = "1" # status: investigating

                    cachet.new_incidents(name=inc_name, message=inc_msg, status=inc_status,
                                         component_id=i['component_id'], component_status=new_component_state)

    return True


def services_watcher_worker(service_map, interval, event):
    """
    Worker for services_watcher. Run it continuously with specific interval
    @param service_map: list of tuples
    @param interval: interval in seconds
    @param event: treading.Event object
    @return:
    """
    logging.info('start services watcher')
    while not event.is_set():
        # Do not run if Zabbix is not available
        if zapi.get_version():
            try:
                services_watcher(service_map)
            except Exception as e:
                logging.error('services_watcher() raised an Exception. Something gone wrong')
                logging.error(e, exc_info=True)
        else:
            logging.error('Zabbix is not available. Skip checking...')
        time.sleep(interval)
    logging.info('end services watcher')


def init_cachet(services):
    """
    Init Cachet by syncing Zabbix service to it
    Also func create mapping batten Cachet components and Zabbix IT services
    @param services: list
    @return: list of tuples
    """
    # Zabbix Services to Cachet components id map
    data = []
    for zbx_service in services:
        zxb2cachet_i = {}
        if zbx_service['children']: 
            group = cachet.new_components_gr(zbx_service['name'])
            for child in zbx_service['children']: 
                if not (child['children']):
                    if not type(child['status']) is str:
                        logging.error('Failed to get status {} from Zabbix'.format(child['status']))
                        continue

                    tags = []
                    for tag in child['tags']:
                        if isinstance(tag, dict):
                            if (tag['tag'] == FILTERTAG):
                                tags.append(tag['value'])

                    tags.sort()
                    tags_str = ",".join(tags)

                    component = cachet.new_components(child['name'], group_id=group['id'],
                                    link='', description='', tags=tags_str)
                    zxb2cachet_i = {'serviceid': child['serviceid']}
                
                else:
                    component = cachet.new_components(child['name'], group_id=group['id'])
                    zxb2cachet_i = {'serviceid': child['serviceid'], 'hasChilds':'true'}
                zxb2cachet_i.update({'group_id': group['id'],
                                     'group_name': group['name'],
                                     'component_id': component['id'],
                                     'component_name': component['name']
                                     })
                data.append(zxb2cachet_i)
        else:
            if zbx_service['serviceid']:
                if not type(child['status']) is str:
                    logging.error('Failed to get status {} from Zabbix'.format(child['status']))
                    continue
                tags = []
                for tag in child['tags']:
                    if isinstance(tag, dict):
                        if (tag['tag'] == FILTERTAG):
                            tags.append(tag['value'])

                tags.sort()
                tags_str = ",".join(tags)

                component = cachet.new_components(child['name'], group_id=group['id'],
                                link='', description='', tags=tags_str)
                # Create a map of Zabbix Services <> Cachet IDs
                zxb2cachet_i = {'serviceid': zbx_service['serviceid'],
                                'component_id': component['id'],
                                'component_name': component['name']}
            data.append(zxb2cachet_i)
    return data


def read_config(config_f):
    """
    Read config file
    @param config_f: strung
    @return: dict of data
    """
    try:
        return yaml.safe_load(open(config_f, "r"))
    except (yaml.error.MarkedYAMLError, IOError) as e:
        logging.error('Failed to parse config file {}: {}'.format(config_f, e))
    return None


if __name__ == '__main__':

    if os.getenv('CONFIG_FILE') is not None:
        CONFIG_F = os.environ['CONFIG_FILE']
    else:
        CONFIG_F = os.path.dirname(os.path.realpath(__file__)) + '/config.yml'
    config = read_config(CONFIG_F)
    if not config:
        sys.exit(1)
    ZABBIX = config['zabbix']
    CACHET = config['cachet']
    SETTINGS = config['settings']

    if SETTINGS.get('time_zone'):
        tz = pytz.timezone(SETTINGS['time_zone'])
    else:
        tz = None

    FILTERTAG = SETTINGS['filtertag']
    # Templates for incident displaying
    acknowledgement_tmpl_d = "{message}\n\n###### {ack_time} by {author}\n\n______\n"
    templates = config.get('templates')
    if templates:
        acknowledgement_tmpl = templates.get('acknowledgement', acknowledgement_tmpl_d)
        investigating_tmpl = templates.get('investigating', '')
        resolving_tmpl = templates.get('resolving', '')
        servicedown_tmpl = templates.get('servicedown','')
    else:
        acknowledgement_tmpl = acknowledgement_tmpl_d

    exit_status = 0
    # Set Logging
    log_level = logging.getLevelName(SETTINGS['log_level'])
    log_level_requests = logging.getLevelName(SETTINGS['log_level_requests'])
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s %(levelname)s: (%(threadName)s) %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S %Z'
    )
    logging.getLogger("requests").setLevel(log_level_requests)
    logging.info('Zabbix Cachet v.{} started'.format(__version__))
    inc_update_t = threading.Thread()
    event = threading.Event()
    try:
        zapi = Zabbix(ZABBIX['server'], ZABBIX['user'], ZABBIX['pass'], ZABBIX['https-verify'])
        cachet = Cachet(CACHET['server'], CACHET['token'], CACHET['https-verify'])
        logging.info('Zabbix ver: {}. Cachet ver: {}'.format(zapi.version, cachet.version))
        zbxtr2cachet = ''
        while True:
            logging.debug('Getting list of Zabbix IT Services ...')
            itservices = (zapi.get_itservices(SETTINGS['root_service']))
            logging.debug('Zabbix IT Services: {}'.format(itservices))
            # Create Cachet components and components groups
            logging.debug('Syncing Zabbix with Cachet...')
            zbxtr2cachet_new = init_cachet(itservices)
            if not zbxtr2cachet_new:
                logging.error('Sorry, can not create Zabbix <> Cachet mapping for you. Please check above errors')
                # Exit if it's a initial run
                if not zbxtr2cachet:
                    sys.exit(1)
                else:
                    zbxtr2cachet_new = zbxtr2cachet
            else:
                logging.info('Successfully synced Cachet components with Zabbix Services')
            # Restart services_watcher_worker
            if zbxtr2cachet != zbxtr2cachet_new:
                zbxtr2cachet = zbxtr2cachet_new
                logging.info('Restart services_watcher worker')
                logging.debug('List of watching services {}'.format(str(zbxtr2cachet)))
                event.set()
                # Wait until tread die
                while inc_update_t.is_alive():
                    time.sleep(1)
                event.clear()
                inc_update_t = threading.Thread(name='Services Watcher',
                                                target=services_watcher_worker,
                                                args=(zbxtr2cachet, SETTINGS['update_inc_interval'], event))
                inc_update_t.daemon = True
                inc_update_t.start()
            time.sleep(SETTINGS['update_comp_interval'])

    except KeyboardInterrupt:
        event.set()
        logging.info('Shutdown requested. See you.')
    except Exception as error:
        logging.error(error)
        exit_status = 1
    sys.exit(exit_status)
