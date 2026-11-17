"""Process-scoped, fail-closed loopback transport for native navigation recovery."""
import hashlib
from collections import Counter
import os
from pathlib import Path
import xml.etree.ElementTree as ET

NAMESPACE='http://www.eprosima.com/XMLSchemas/fastRTPS_Profiles'


def loopback_environment(environment, profile):
    """Validate our UDP-only profile before replacing Humble's implicit SHM transport."""
    profile=Path(profile).resolve(strict=True)
    data=profile.read_bytes()
    if len(data)>16384 or b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
        raise ValueError('Unsupported DDS profile document')
    root=ET.fromstring(data)
    allowed={'dds','profiles','transport_descriptors','transport_descriptor','transport_id','type',
             'interfaceWhiteList','address','participant','rtps','userTransports','useBuiltinTransports'}
    for element in root.iter():
        if not element.tag.startswith('{'+NAMESPACE+'}') or element.tag.split('}',1)[1] not in allowed:
            raise ValueError('Unsupported DDS profile element')
        if element is not root and element.tag!='{'+NAMESPACE+'}participant' and element.attrib:
            raise ValueError('Unexpected DDS profile attributes')
    counts=Counter(element.tag.split('}',1)[1] for element in root.iter())
    expected={name:1 for name in allowed};expected['transport_id']=2
    if counts!=expected:raise ValueError('Unexpected or duplicated DDS profile structure')
    if root.attrib:raise ValueError('Unexpected root attributes')
    ns={'d':NAMESPACE}
    descriptors=root.findall('d:profiles/d:transport_descriptors/d:transport_descriptor',ns)
    participants=root.findall('d:profiles/d:participant',ns)
    if len(descriptors)!=1 or len(participants)!=1:
        raise ValueError('Exactly one loopback transport and participant are required')
    descriptor,participant=descriptors[0],participants[0]
    if set(participant.attrib)!={'profile_name','is_default_profile'}:
        raise ValueError('Unexpected participant attributes')
    text=lambda node,path:node.findtext(path,default='',namespaces=ns).strip()
    if text(descriptor,'d:type')!='UDPv4':raise ValueError('Only UDPv4 is permitted')
    if text(descriptor,'d:transport_id')!='rmsim_loopback_udp':raise ValueError('Unknown loopback transport identity')
    addresses=descriptor.findall('d:interfaceWhiteList/d:address',ns)
    if len(addresses)!=1 or (addresses[0].text or '').strip()!='127.0.0.1':
        raise ValueError('DDS interface whitelist must contain only 127.0.0.1')
    if participant.get('is_default_profile')!='true' or text(participant,'d:rtps/d:useBuiltinTransports')!='false':
        raise ValueError('Default participant must disable built-in transports')
    transports=participant.findall('d:rtps/d:userTransports/d:transport_id',ns)
    if len(transports)!=1 or (transports[0].text or '').strip()!=text(descriptor,'d:transport_id'):
        raise ValueError('Participant must use the single validated transport')
    if environment.get('RMW_IMPLEMENTATION','rmw_fastrtps_cpp')!='rmw_fastrtps_cpp':
        raise ValueError('This transport profile requires rmw_fastrtps_cpp')
    if environment.get('FASTDDS_ENVIRONMENT_FILE'):
        raise ValueError('An external Fast DDS environment file conflicts with the local profile')
    result=dict(environment)
    result['FASTRTPS_DEFAULT_PROFILES_FILE']=str(profile)
    result['FASTDDS_DEFAULT_PROFILES_FILE']=str(profile)
    # Humble's localhost flag adds SHM after reading XML. Our validated XML preserves
    # the same loopback interface boundary while selecting UDP without shared memory.
    result['ROS_LOCALHOST_ONLY']='0'
    return result,{'mode':'loopback_udp','allowed_interfaces':['127.0.0.1'],
                   'profile_sha256':hashlib.sha256(data).hexdigest(),'profile':str(profile)}


def configure_navigation_transport():
    mode=os.environ.get('RMSIM_NAV_TRANSPORT','loopback_udp' if os.name=='nt' else 'default')
    if mode=='default':return {'mode':'default','ros_localhost_only':os.environ.get('ROS_LOCALHOST_ONLY')}
    if mode!='loopback_udp':raise ValueError('RMSIM_NAV_TRANSPORT must be default or loopback_udp')
    from ament_index_python.packages import get_package_share_directory
    profile=Path(get_package_share_directory('rmsim_bridge'))/'config/fastdds-loopback-udp.xml'
    environment,report=loopback_environment(os.environ,profile)
    os.environ.update(environment)
    return report
