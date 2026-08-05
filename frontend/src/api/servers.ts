import request from './request'

export interface ServerConnection { id:number; server_id:number; server_name?:string; nic_id:number; nic_name:string; mac_address:string; switch_device_id?:number; switch_name?:string; switch_ip?:string; switch_port:string; state:'candidate'|'confirmed'|'rejected'|'stale'; confidence:number; confidence_level:'low'|'medium'|'high'; evidence:Array<{source:string;observed_at?:string;details?:Record<string,unknown>}>; conflict_reasons:string[]; last_discovered_at?:string; confirmed_by?:string; confirmed_at?:string }
export interface ServerIP { id?:number; address:string; prefix_length:number; vlan_id?:number; network_type:string; is_primary?:boolean }
export interface ServerNIC { id:number; server_id?:number; server_name?:string; management_ip?:string; name:string; mac_address:string; speed_mbps?:number; bond_name?:string; network_type:string; mtu?:number; status:string; ip_addresses:ServerIP[]; connections:ServerConnection[] }
export interface ServerAsset { id:number; name:string; management_ip?:string; serial_number?:string; vendor?:string; model?:string; asset_tag?:string; status:string; datacenter_id?:number; datacenter_name?:string; rack?:string; rack_unit?:string; operating_system?:string; cpu_summary?:string; memory_gb?:number; storage_summary?:string; gpu_summary?:string; bmc_type?:string; bmc_address?:string; owner?:string; business_system?:string; nic_count:number; connection_count:number; nics?:ServerNIC[]; components?:any[] }
export interface ServerPortChange { id:number; connection_id:number; server_name?:string; switch_name?:string; switch_ip?:string; switch_port?:string; status:string; requested_config:Record<string,unknown>; existing_config:Record<string,unknown>; config_diff:unknown[]; precheck_result:Record<string,unknown>; requested_by:string; requested_at:string; execution_enabled:boolean }

export const getServers=(params?:Record<string,unknown>)=>request.get('/servers',{params}) as Promise<{total:number;items:ServerAsset[]}>
export const getServer=(id:number)=>request.get(`/servers/${id}`) as Promise<ServerAsset>
export const createServer=(data:Partial<ServerAsset>)=>request.post('/servers',data) as Promise<ServerAsset>
export const createServerWithNetwork=(data:Record<string,unknown>)=>request.post('/servers/with-network',data) as Promise<ServerAsset>
export const getServerNICs=(params?:Record<string,unknown>)=>request.get('/servers/nics',{params}) as Promise<{total:number;items:ServerNIC[]}>
export const getServerConnections=(params?:Record<string,unknown>)=>request.get('/servers/connections',{params}) as Promise<{total:number;items:ServerConnection[]}>
export const getServerPortChanges=(params?:Record<string,unknown>)=>request.get('/servers/port-changes',{params}) as Promise<{total:number;items:ServerPortChange[]}>
export const addServerNIC=(id:number,data:Record<string,unknown>)=>request.post(`/servers/${id}/nics`,data)
export const addServerIP=(nicId:number,data:Record<string,unknown>)=>request.post(`/servers/nics/${nicId}/ip-addresses`,data)
export const confirmConnection=(id:number,note?:string)=>request.post(`/servers/connections/${id}/confirm`,{note})
export const rejectConnection=(id:number,note?:string)=>request.post(`/servers/connections/${id}/reject`,{note})
export const createPortChange=(data:Record<string,unknown>)=>request.post('/servers/port-changes',data)
export const importServers=(file:File)=>{const body=new FormData();body.append('file',file);return request.post('/servers/import',body,{headers:{'Content-Type':'multipart/form-data'}}) as Promise<{created:number;failed:number;errors:any[]}>}
