"""SNMP related helper functions."""

import pysnmp.hlapi.asyncio as snmp

async def snmp_query(ip: str, oid: str, community: str = "public") -> str:
    """Perform an asynchronous SNMP GET request.

    Returns the value as a string or ``"Error"`` if the query fails.
    """
    try:
        iterator = snmp.getCmd(
            snmp.SnmpEngine(),
            snmp.CommunityData(community),
            snmp.UdpTransportTarget((ip, 161)),
            snmp.ContextData(),
            snmp.ObjectType(snmp.ObjectIdentity(oid)),
        )
        errorIndication, errorStatus, errorIndex, varBinds = await iterator
        if errorIndication or errorStatus:
            return "Error"
        for varBind in varBinds:
            return str(varBind[1])
    except Exception:
        return "Error"
