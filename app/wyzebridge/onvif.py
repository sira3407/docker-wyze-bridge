import os
import socket
import struct
import uuid
from datetime import UTC, datetime
from urllib.parse import urlparse
from xml.etree import ElementTree

from flask import request
from wyzebridge import config
from wyzebridge.bridge_utils import env_bool
from wyzebridge.logging import logger

NAMESPACES = {
    "s": "http://www.w3.org/2003/05/soap-envelope",
    "wsdl": "http://www.onvif.org/ver10/media/wsdl",
}


def parse_message_id(soap_message):
    try:
        root = ElementTree.fromstring(soap_message)
        namespace = {"w": "http://schemas.xmlsoap.org/ws/2004/08/addressing"}
        message_id_element = root.find(".//w:MessageID", namespaces=namespace)
        return message_id_element.text if message_id_element is not None else None
    except ElementTree.ParseError:
        return None


def ws_discovery(server):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", 3702))

    mreq = struct.pack("4sl", socket.inet_aton("239.255.255.250"), socket.INADDR_ANY)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

    logger.info("Listening for WS-Discovery messages...")

    while True:
        data, addr = sock.recvfrom(1024)
        request_id = parse_message_id(data.decode("utf-8"))
        response_uuid = str(uuid.uuid4())
        response = """<?xml version="1.0" encoding="utf-8"?>
        <soapenv:Envelope xmlns:soapenv="http://www.w3.org/2003/05/soap-envelope" 
                  xmlns:tds="http://www.onvif.org/ver10/device/wsdl" 
                  xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
                  xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
                  xmlns:wsa="http://www.w3.org/2005/08/addressing">"""

        if request_id:
            response += f"""
    <soapenv:Header>
            <wsa:MessageID>{response_uuid}</wsa:MessageID>
            <wsa:Action>http://www.onvif.org/ver10/device/wsdl/ProbeMatches</wsa:Action>
        <wsa:RelatesTo>{request_id}</wsa:RelatesTo>
    </soapenv:Header>"""

        response += f"""
        <soapenv:Body>
        <d:ProbeMatches>
            <d:ProbeMatch>
                <d:Types>dn:NetworkVideoTransmitter</d:Types>
                <d:Scopes>onvif://www.onvif.org/Profile/Streaming</d:Scopes>
                <d:XAddrs>http://{server}:5000/onvif/device_service</d:XAddrs>
                <d:MetadataVersion>1</d:MetadataVersion>
            </d:ProbeMatch>
        </d:ProbeMatches>
    </soapenv:Body>
</soapenv:Envelope>"""

        sock.sendto(response.encode("utf-8"), addr)


def parse_action(xml_request):
    onvif_path = os.path.basename(request.path)
    try:
        root = ElementTree.fromstring(xml_request)
        namespace = {"s": NAMESPACES["s"]}
        body = root.find(".//s:Body", namespace)
        if body is not None and len(body):
            action_element = body[0]
            action = action_element.tag.rsplit("}", 1)[-1]
            token = action_element.find(".//wsdl:ProfileToken", NAMESPACES)
            profile = token.text if token is not None else None
            logger.debug(f"{onvif_path=}, {action=}, {profile=}, {xml_request=}")
            return action, profile
    except ElementTree.ParseError as e:
        logger.error(f"XML parsing error: {e}")
    return None, None


def onvif_resp(streams):
    action, profile_token = parse_action(request.data)

    if action == "GetProfiles":
        resp = get_profiles(streams.streams)
    elif action == "GetVideoSources":
        resp = get_video_sources()
    elif action == "GetStreamUri":
        resp = get_stream_uri(profile_token)
    elif action == "GetSnapshotUri":
        resp = get_snapshot_uri(profile_token)
    elif action == "GetSystemDateAndTime":
        resp = get_system_date_and_time()
    elif action == "GetServices":
        resp = get_services()
    elif action == "GetCapabilities":
        resp = get_capabilities()
    elif action == "GetServiceCapabilities":
        resp = get_service_capabilities()
    elif action == "GetVideoEncoderConfigurationOptions":
        resp = get_video_encoder_options()
    elif action == "GetVideoEncoderConfiguration":
        resp = get_video_encoder()
    elif action == "SetVideoEncoderConfiguration":
        resp = set_video_encoder_config()
    elif action == "GetConfigurations":
        resp = get_configurations()
    elif action == "GetDeviceInformation":
        resp = get_device_information()
    elif action == "GetNetworkInterfaces":
        resp = get_network_interfaces()
    elif action == "GetPresets":
        resp = get_presets()
    elif action == "Subscribe":
        resp = subscribe()
    else:
        resp = unknown_request()

    return f"""<?xml version="1.0" encoding="utf-8"?>
<soapenv:Envelope xmlns:soapenv="http://www.w3.org/2003/05/soap-envelope" 
                  xmlns:tds="http://www.onvif.org/ver10/device/wsdl" 
                  xmlns:trt="http://www.onvif.org/ver10/media/wsdl"
                  xmlns:tt="http://www.onvif.org/ver10/schema">
    <soapenv:Body>
        {resp}
    </soapenv:Body>
</soapenv:Envelope>"""


def get_system_date_and_time():
    now = datetime.now(UTC)
    return f"""<tds:GetSystemDateAndTimeResponse>
            <tt:SystemDateAndTime>
                <tt:DateTimeType>NTP</tt:DateTimeType>
                <tt:UTCDateTime>
                    <tt:Time>
                        <tt:Hour>{now.hour}</tt:Hour>
                        <tt:Minute>{now.minute}</tt:Minute>
                        <tt:Second>{now.second}</tt:Second>
                    </tt:Time>
                    <tt:Date>
                        <tt:Year>{now.year}</tt:Year>
                        <tt:Month>{now.month}</tt:Month>
                        <tt:Day>{now.day}</tt:Day>
                    </tt:Date>
                </tt:UTCDateTime>
            </tt:SystemDateAndTime>
        </tds:GetSystemDateAndTimeResponse>"""


def get_services():
    hostname = env_bool("DOMAIN", urlparse(request.root_url).hostname or "localhost")
    return f"""<tds:GetServicesResponse>
            <tds:Service>
                <tds:Namespace>http://www.onvif.org/ver10/device/wsdl</tds:Namespace>
                <tds:XAddr>http://{hostname}:5000/onvif/device_service</tds:XAddr>
                <tds:Version>
                    <tds:Major>2</tds:Major>
                    <tds:Minor>4</tds:Minor>
                </tds:Version>
                <tds:Capabilities>
                    <tds:Discovery>true</tds:Discovery>
                </tds:Capabilities>
            </tds:Service>
            <tds:Service>
                <tds:Namespace>http://www.onvif.org/ver10/media/wsdl</tds:Namespace>
                <tds:XAddr>http://{hostname}:5000/onvif/media_service</tds:XAddr>
                <tds:Version>
                    <tds:Major>2</tds:Major>
                    <tds:Minor>4</tds:Minor>
                </tds:Version>
                <tds:Capabilities>
                    <tds:StreamingCapabilities>
                        <tds:RTPMulticast>false</tds:RTPMulticast>
                        <tds:RTP_TCP>false</tds:RTP_TCP>
                        <tds:RTP_RTSP_TCP>true</tds:RTP_RTSP_TCP>
                    </tds:StreamingCapabilities>
                </tds:Capabilities>
            </tds:Service>
        </tds:GetServicesResponse>"""


def get_configurations():
    return """<tds:GetConfigurationsResponse>
            <trt:VideoSource token="VideoSourceConfig_1">
                <trt:Name>PrimaryVideo</trt:Name>
                <trt:SourceToken>VideoSource_1</trt:SourceToken>
                <trt:Bounds x="0" y="0" width="1920" height="1080"/>
            </trt:VideoSource>
            <trt:AudioSource token="AudioSourceConfig_1">
                <trt:Name>Audio Source</trt:Name>
                <trt:SourceToken>AudioSource_1</trt:SourceToken>
            </trt:AudioSource>
            <trt:VideoEncoder token="VideoEncoderConfig_1">
                <trt:Name>Video Encoder</trt:Name>
                <trt:Encoding>H264</trt:Encoding>
                <trt:Resolution>
                    <tt:Width>1920</tt:Width>
                    <tt:Height>1080</tt:Height>
                </trt:Resolution>
                <trt:Bitrate>2048</trt:Bitrate>
                <trt:FrameRate>30</trt:FrameRate>
            </trt:VideoEncoder>
            <trt:AudioEncoderConfiguration token="AudioEncoderConfig_1">
                <trt:Name>Audio Encoder</trt:Name>
                <trt:Encoding>G711</trt:Encoding>
                <trt:Bitrate>64</trt:Bitrate>
                <trt:SampleRate>8000</trt:SampleRate>
            </trt:AudioEncoderConfiguration>
        </tds:GetConfigurationsResponse>"""


def get_capabilities():
    hostname = env_bool("DOMAIN", urlparse(request.root_url).hostname or "localhost")
    return f"""<tds:GetCapabilitiesResponse>
            <tds:Capabilities>
                <trt:Media>
                    <trt:XAddr>http://{hostname}:5000/onvif/media_service</trt:XAddr>
                    <trt:StreamingCapabilities>
                        <tt:RTPMulticast>false</tt:RTPMulticast>
                        <tt:RTP_TCP>false</tt:RTP_TCP>
                        <tt:RTP_RTSP_TCP>true</tt:RTP_RTSP_TCP>
                    </trt:StreamingCapabilities>
                </trt:Media>
                <tds:Device>
                    <tds:XAddr>http://{hostname}:5000/onvif/device_service</tds:XAddr>
                    <tds:System>
                        <tt:DiscoveryResolve>true</tt:DiscoveryResolve>
                        <tt:DiscoveryBye>false</tt:DiscoveryBye>
                        <tt:RemoteDiscovery>false</tt:RemoteDiscovery>
                        <tt:SystemBackup>false</tt:SystemBackup>
                        <tt:SystemLogging>false</tt:SystemLogging>
                        <tt:FirmwareUpgrade>false</tt:FirmwareUpgrade>
                        <tt:SupportedVersions>
                            <tt:Major>2</tt:Major>
                            <tt:Minor>06</tt:Minor>
                        </tt:SupportedVersions>
                    </tds:System>
                </tds:Device>
            </tds:Capabilities>
        </tds:GetCapabilitiesResponse>"""


def get_service_capabilities():
    return """<tds:GetServiceCapabilitiesResponse>
            <tds:Capabilities>
                <tds:Device>
                    <tds:SystemCapabilities>
                        <tds:DiscoveryResolve>true</tds:DiscoveryResolve>
                        <tds:DiscoveryBye>false</tds:DiscoveryBye>
                        <tds:RemoteDiscovery>false</tds:RemoteDiscovery>
                    </tds:SystemCapabilities>
                    <tds:UserManagement>false</tds:UserManagement>
                    <tds:Extension/>
                </tds:Device>
                <tds:Media>
                    <tds:StreamingCapabilities>
                        <tds:RTPMulticast>false</tds:RTPMulticast>
                        <tds:RTP_TCP>false</tds:RTP_TCP>
                        <tds:RTP_RTSP_TCP>true</tds:RTP_RTSP_TCP>
                    </tds:StreamingCapabilities>
                    <tds:VideoEncoderConfigurationOptions>
                        <tds:GuaranteedFrameRateSupported>false</tds:GuaranteedFrameRateSupported>
                    </tds:VideoEncoderConfigurationOptions>
                    <tds:Extension/>
                </tds:Media>
                <tds:Extension/>
            </tds:Capabilities>
        </tds:GetServiceCapabilitiesResponse>"""


def get_video_encoder_options():
    return """<trt:GetVideoEncoderConfigurationOptionsResponse>
            <trt:Options>
                <tt:QualityRange>
                    <tt:Min>1</tt:Min>
                    <tt:Max>100</tt:Max>
                </tt:QualityRange>
                <tt:H264>
                    <tt:ResolutionsAvailable>
                        <tt:Width>1920</tt:Width>
                        <tt:Height>1080</tt:Height>
                    </tt:ResolutionsAvailable>
                    <tt:GovLengthRange>
                        <tt:Min>1</tt:Min>
                        <tt:Max>120</tt:Max>
                    </tt:GovLengthRange>
                    <tt:FrameRateRange>
                        <tt:Min>1</tt:Min>
                        <tt:Max>30</tt:Max>
                    </tt:FrameRateRange>
                    <tt:EncodingIntervalRange>
                        <tt:Min>1</tt:Min>
                        <tt:Max>100</tt:Max>
                    </tt:EncodingIntervalRange>
                    <tt:H264ProfilesSupported>Baseline</tt:H264ProfilesSupported>
                    <tt:H264ProfilesSupported>Main</tt:H264ProfilesSupported>
                    <tt:H264ProfilesSupported>High</tt:H264ProfilesSupported>
                </tt:H264>
            </trt:Options>
        </trt:GetVideoEncoderConfigurationOptionsResponse>"""


def get_video_encoder():
    return """<trt:GetVideoEncoderConfigurationResponse>
            <tt:Configuration>
                <tt:Encoding>H264</tt:Encoding>
                <tt:Resolution>
                    <tt:Width>1920</tt:Width>
                    <tt:Height>1080</tt:Height>
                </tt:Resolution>
                <tt:Quality>75</tt:Quality>
                <tt:FrameRate>30</tt:FrameRate>
                <tt:EncodingInterval>1</tt:EncodingInterval>
                <tt:Bitrate>2048</tt:Bitrate>
                <tt:ProfileToken>carport</tt:ProfileToken>
            </tt:Configuration>
        </trt:GetVideoEncoderConfigurationResponse>"""


def set_video_encoder_config():
    return """<trt:SetVideoEncoderConfigurationResponse/>"""


def get_video_sources():
    return """<trt:GetVideoSourcesResponse>
            <trt:VideoSources token="VideoSource_1">
                <tt:Framerate>30</tt:Framerate>
                <tt:Resolution>
                    <tt:Width>1920</tt:Width>
                    <tt:Height>1080</tt:Height>
                </tt:Resolution>
            </trt:VideoSources>
        </trt:GetVideoSourcesResponse>"""


def get_profiles(streams):
    resp = """<trt:GetProfilesResponse>
            """
    for stream in streams:
        resp += f"""<trt:Profiles token="{stream}" fixed="true">
                <tt:Name>{stream}</tt:Name>
                <tt:VideoSourceConfiguration token="VideoSourceConfig_1">
                    <tt:SourceToken>VideoSource_1</tt:SourceToken>
                    <tt:Bounds x="0" y="0" width="1920" height="1080"/>
                </tt:VideoSourceConfiguration>
                <tt:VideoEncoderConfiguration token="VideoEncoderConfig_1">
                    <tt:Encoding>H264</tt:Encoding>
                    <tt:Resolution>
                        <tt:Width>1920</tt:Width>
                        <tt:Height>1080</tt:Height>
                    </tt:Resolution>
                </tt:VideoEncoderConfiguration>
            </trt:Profiles>
            """
    return resp + "</trt:GetProfilesResponse>"


def get_stream_uri(profile):
    hostname = env_bool("DOMAIN", urlparse(request.root_url).hostname or "localhost")
    return f"""<trt:GetStreamUriResponse>
            <trt:MediaUri>
                <tt:Uri>rtsp://{hostname}:8554/{profile}</tt:Uri>
                <tt:InvalidAfterConnect>false</tt:InvalidAfterConnect>
                <tt:InvalidAfterReboot>false</tt:InvalidAfterReboot>
                <tt:Timeout>PT60S</tt:Timeout>
            </trt:MediaUri>
        </trt:GetStreamUriResponse>"""


def get_snapshot_uri(profile):
    hostname = env_bool("DOMAIN", urlparse(request.root_url).hostname or "localhost")
    return f"""<trt:GetSnapshotUriResponse>
            <trt:MediaUri>
                <tt:Uri>http://{hostname}:5000/snapshot/{profile}.jpg</tt:Uri>
                <tt:InvalidAfterConnect>false</tt:InvalidAfterConnect>
                <tt:InvalidAfterReboot>false</tt:InvalidAfterReboot>
                <tt:Timeout>PT60S</tt:Timeout>
            </trt:MediaUri>
        </trt:GetSnapshotUriResponse>"""


def get_device_information():
    return f"""<tds:GetDeviceInformationResponse>
            <tds:Manufacturer>Wyze Bridge</tds:Manufacturer>
            <tds:Model>{config.BUILD} Build [{config.ARCH}]</tds:Model> 
            <tds:FirmwareVersion>v{config.VERSION}</tds:FirmwareVersion>
            <tds:SerialNumber>0</tds:SerialNumber> 
            <tds:HardwareId>{config.BUILD_STR}</tds:HardwareId> 
        </tds:GetDeviceInformationResponse>"""


def get_network_interfaces():
    return """<tds:GetNetworkInterfacesResponse>
            <tds:NetworkInterfaces token="eth0">
                <tt:Enabled>false</tt:Enabled>
            </tds:NetworkInterfaces>
        </tds:GetNetworkInterfacesResponse>"""


def get_presets():
    return """<trt:GetPresetsResponse>
            <tt:Presets/>
        </trt:GetPresetsResponse>"""


def subscribe():
    return """<soapenv:Fault>
            <soapenv:Code>
                <soapenv:Value>soapenv:Sender</soapenv:Value>
                <soapenv:Subcode>
                    <soapenv:Value>SubscribeNotAllowed</soapenv:Value>
                </soapenv:Subcode>
            </soapenv:Code>
            <soapenv:Reason>
                <soapenv:Text xml:lang="en">Subscription is not allowed on this device.</soapenv:Text>
            </soapenv:Reason>
        </soapenv:Fault>"""


def unknown_request():
    return """<soapenv:Fault>
            <soapenv:Code>
                <soapenv:Value>soapenv:Sender</soapenv:Value>
                <soapenv:Subcode>
                    <soapenv:Value>soapenv:NotUnderstood</soapenv:Value>
                </soapenv:Subcode>
            </soapenv:Code>
            <soapenv:Reason>
                <soapenv:Text xml:lang="en">The requested command is not supported by this device.</soapenv:Text>
            </soapenv:Reason>
        </soapenv:Fault>"""