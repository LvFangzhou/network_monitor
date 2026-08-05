import { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Card, Descriptions, Drawer, Empty, Form, Input, InputNumber, Modal, Popover, Select, Space, Statistic, Table, Tabs, Tag, Upload, message } from 'antd'
import { CloudUploadOutlined, DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import { useSearchParams } from 'react-router-dom'
import {
  addServerIP, addServerNIC, confirmConnection, createPortChange, createServerWithNetwork,
  getServer, getServerConnections, getServerNICs, getServerPortChanges, getServers, importServers,
  rejectConnection, type ServerAsset, type ServerConnection, type ServerNIC, type ServerPortChange,
} from '../../api/servers'
import { getDatacenters, type Datacenter } from '../../api/devices'
import { useAuthStore } from '../../store/auth'

const statusText:Record<string,string>={active:'运行中',in_stock:'库存',deployed:'已上架',maintenance:'维护中',retired:'已退役'}
const sourceText:Record<string,string>={lldp:'LLDP',mac_table:'MAC表',arp:'ARP表',redfish:'Redfish',agent:'Agent',manual:'人工'}
const networkTypeOptions=[{value:'business',label:'业务网'},{value:'management',label:'管理网'},{value:'parameter',label:'参数网'},{value:'storage',label:'存储网'},{value:'roce',label:'RoCE网'}]
const connectionState:Record<string,string>={candidate:'待确认',confirmed:'已确认',rejected:'已拒绝',stale:'待复核'}
const changeState:Record<string,string>={draft:'草稿',precheck_failed:'预检未通过',precheck_passed:'预检通过',approved:'已审批',rejected:'已拒绝',completed:'已完成',failed:'执行失败'}

function NetworkEmpty({canModify,onCreate}:{canModify:boolean;onCreate:()=>void}){
  return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有网卡与网络数据">
    <Space direction="vertical">
      <span style={{color:'#64748b'}}>新增服务器时可同时录入多张网卡、MAC、IP、VLAN、MTU 和 Bond。</span>
      {canModify&&<Button type="primary" icon={<PlusOutlined/>} onClick={onCreate}>新增服务器并录入网卡</Button>}
    </Space>
  </Empty>
}

export default function ServerList(){
  const [searchParams,setSearchParams]=useSearchParams()
  const canModify=!useAuthStore(s=>s.user?.read_only)
  const [tab,setTab]=useState(searchParams.get('view')||'assets')
  const [rows,setRows]=useState<ServerAsset[]>([]),[total,setTotal]=useState(0),[loading,setLoading]=useState(false)
  const [nics,setNics]=useState<ServerNIC[]>([]),[connections,setConnections]=useState<ServerConnection[]>([]),[changes,setChanges]=useState<ServerPortChange[]>([])
  const [search,setSearch]=useState(''),[dc,setDc]=useState<number>(),[dcs,setDcs]=useState<Datacenter[]>([])
  const [detail,setDetail]=useState<ServerAsset>(),[formOpen,setFormOpen]=useState(false),[change,setChange]=useState<ServerConnection>()
  const [nicOpen,setNicOpen]=useState(false),[ipNic,setIpNic]=useState<ServerNIC>()
  const [form]=Form.useForm(),[changeForm]=Form.useForm(),[nicForm]=Form.useForm(),[ipForm]=Form.useForm()

  const openCreate=()=>{form.resetFields();form.setFieldsValue({status:'in_stock',nics:[]});setFormOpen(true)}
  const load=async()=>{
    setLoading(true)
    try{
      const [assets,nicRows,linkRows,changeRows]=await Promise.all([
        getServers({search:search||undefined,datacenter_id:dc,limit:500}),
        getServerNICs({search:search||undefined,limit:1000}),getServerConnections({limit:1000}),getServerPortChanges({limit:1000}),
      ])
      setRows(assets.items);setTotal(assets.total);setNics(nicRows.items);setConnections(linkRows.items);setChanges(changeRows.items)
    }catch{message.error('服务器管理数据读取失败')}finally{setLoading(false)}
  }
  useEffect(()=>{getDatacenters().then(setDcs);const id=Number(searchParams.get('server_id'));if(id>0)getServer(id).then(setDetail).catch(()=>message.error('URL 中的服务器不存在'))},[])
  useEffect(()=>{const t=window.setTimeout(load,250);return()=>window.clearTimeout(t)},[search,dc])
  const switchTab=(key:string)=>{setTab(key);const next=new URLSearchParams(searchParams);next.set('view',key);setSearchParams(next,{replace:true})}
  const openDetail=async(id:number)=>{setDetail(await getServer(id));const next=new URLSearchParams(searchParams);next.set('server_id',String(id));setSearchParams(next,{replace:true})}
  const closeDetail=()=>{setDetail(undefined);const next=new URLSearchParams(searchParams);next.delete('server_id');setSearchParams(next,{replace:true})}
  const reload=async()=>{await load();if(detail)setDetail(await getServer(detail.id))}
  const detailLinks=(detail?.nics||[]).flatMap(n=>n.connections||[])
  const ipCount=useMemo(()=>nics.reduce((sum,n)=>sum+(n.ip_addresses?.length||0),0),[nics])

  const connectionColumns:any[]=[
    {title:'服务器 / 网卡',render:(_:unknown,r:ServerConnection)=><div><b>{r.server_name||detail?.name||'-'}</b><br/>{r.nic_name} · {r.mac_address}</div>},
    {title:'交换机 / 端口',render:(_:unknown,r:ServerConnection)=><div><b>{r.switch_name||'-'}</b><br/>{r.switch_ip||'-'} · {r.switch_port}</div>},
    {title:'连接证据',render:(_:unknown,r:ServerConnection)=><Popover title="连接发现证据" placement="left" content={<pre style={{maxWidth:520,maxHeight:360,overflow:'auto',whiteSpace:'pre-wrap'}}>{JSON.stringify({证据:r.evidence,冲突:r.conflict_reasons},null,2)}</pre>}><Space wrap>{(r.evidence||[]).map((x,i)=><Tag key={i}>{sourceText[x.source]||x.source}</Tag>)}</Space></Popover>},
    {title:'可信度',render:(_:unknown,r:ServerConnection)=><Tag color={r.confidence_level==='high'?'green':r.confidence_level==='medium'?'orange':'red'}>{r.confidence.toFixed(0)}分 · {r.confidence_level==='high'?'高':r.confidence_level==='medium'?'中':'低'}</Tag>},
    {title:'状态',dataIndex:'state',render:(v:string)=><Tag color={v==='confirmed'?'green':v==='rejected'?'red':v==='candidate'?'gold':'default'}>{connectionState[v]||v}</Tag>},
    {title:'最后发现',dataIndex:'last_discovered_at',render:(v:string)=>v?new Date(v).toLocaleString():'-'},
    {title:'操作',render:(_:unknown,r:ServerConnection)=><Space>{r.state==='candidate'&&canModify&&<><Button size="small" type="primary" onClick={async()=>{try{await confirmConnection(r.id,'服务器管理人工确认');message.success('连接已确认');reload()}catch{message.error('连接存在冲突或可信度不足，不能确认')}}}>确认</Button><Button size="small" danger onClick={async()=>{await rejectConnection(r.id,'人工判定不匹配');reload()}}>拒绝</Button></>}{r.state==='confirmed'&&canModify&&<Button size="small" onClick={()=>{setChange(r);changeForm.resetFields()}}>变更预检</Button>}</Space>},
  ]

  const tabs=[
    {key:'assets',label:`服务器资产 (${total})`,children:<Table rowKey="id" loading={loading} dataSource={rows} locale={{emptyText:<NetworkEmpty canModify={canModify} onCreate={openCreate}/>}} pagination={{pageSize:20,showSizeChanger:true}} columns={[
      {title:'服务器名称',dataIndex:'name',render:(v:string,r:ServerAsset)=><Button type="link" onClick={()=>openDetail(r.id)}>{v}</Button>},{title:'管理IP',dataIndex:'management_ip',render:v=>v||'-'},
      {title:'厂商 / 型号',render:(_:unknown,r:ServerAsset)=>`${r.vendor||'-'} / ${r.model||'-'}`},{title:'SN',dataIndex:'serial_number',render:v=>v||'-'},{title:'机房 / 机柜',render:(_:unknown,r:ServerAsset)=>`${r.datacenter_name||'-'} / ${r.rack||'-'}`},
      {title:'GPU',dataIndex:'gpu_summary',ellipsis:true,render:v=>v||'-'},{title:'网卡',dataIndex:'nic_count'},{title:'连接',dataIndex:'connection_count'},{title:'状态',dataIndex:'status',render:v=><Tag>{statusText[v]||v}</Tag>},
    ]}/>},
    {key:'network',label:`网卡与 IP (${nics.length}/${ipCount})`,children:<Table rowKey="id" loading={loading} dataSource={nics} locale={{emptyText:<NetworkEmpty canModify={canModify} onCreate={openCreate}/>}} pagination={{pageSize:20,showSizeChanger:true}} columns={[
      {title:'服务器',render:(_:unknown,r:ServerNIC)=><div><Button type="link" style={{padding:0}} onClick={()=>r.server_id&&openDetail(r.server_id)}>{r.server_name||'-'}</Button><br/>{r.management_ip||'-'}</div>},
      {title:'网卡',render:(_:unknown,r:ServerNIC)=><div><b>{r.name}</b>{r.bond_name&&<><br/><Tag>Bond {r.bond_name}</Tag></>}</div>},{title:'MAC地址',dataIndex:'mac_address'},
      {title:'网络用途',dataIndex:'network_type',render:v=>networkTypeOptions.find(x=>x.value===v)?.label||v},{title:'速率 / MTU',render:(_:unknown,r:ServerNIC)=><>{r.speed_mbps?`${r.speed_mbps} Mbps`:'-'} / {r.mtu||'-'}</>},
      {title:'IP / VLAN',render:(_:unknown,r:ServerNIC)=><Space wrap>{(r.ip_addresses||[]).map((x,i)=><Tag color="blue" key={x.id||i}>{x.address}/{x.prefix_length}{x.vlan_id?` · VLAN ${x.vlan_id}`:''}</Tag>)}</Space>},
    ]}/>},
    {key:'connections',label:`连接关系 (${connections.length})`,children:<><Alert showIcon type="info" style={{marginBottom:12}} message="LLDP、MAC表、ARP、Redfish 和 Agent 只生成候选关系；达到可信度门槛且无冲突后，仍需人工确认。"/><Table rowKey="id" loading={loading} dataSource={connections} locale={{emptyText:<Empty description="暂无连接候选；自动发现采集器接通后会在这里形成待确认关系"/>}} pagination={{pageSize:20,showSizeChanger:true}} columns={connectionColumns}/></>},
    {key:'changes',label:`变更工单 (${changes.length})`,children:<><Alert showIcon type="warning" style={{marginBottom:12}} message="当前仅开放差异记录与预检流程；实时读取交换机配置和厂商下发适配器尚未验收，生产执行开关保持关闭。"/><Table rowKey="id" loading={loading} dataSource={changes} locale={{emptyText:<Empty description="暂无端口变更工单；确认连接后可发起变更预检"/>}} pagination={{pageSize:20,showSizeChanger:true}} columns={[
      {title:'工单',dataIndex:'id',render:v=>`#${v}`},{title:'服务器',dataIndex:'server_name',render:v=>v||'-'},{title:'交换机 / 端口',render:(_:unknown,r:ServerPortChange)=>`${r.switch_name||r.switch_ip||'-'} · ${r.switch_port||'-'}`},
      {title:'请求配置',dataIndex:'requested_config',render:v=><Popover content={<pre style={{whiteSpace:'pre-wrap'}}>{JSON.stringify(v,null,2)}</pre>}><Button type="link">查看配置</Button></Popover>},{title:'状态',dataIndex:'status',render:v=><Tag color={v==='precheck_failed'||v==='failed'?'red':v==='completed'?'green':'gold'}>{changeState[v]||v}</Tag>},{title:'申请人 / 时间',render:(_:unknown,r:ServerPortChange)=><>{r.requested_by}<br/>{r.requested_at?new Date(r.requested_at).toLocaleString():'-'}</>},
    ]}/></>},
  ]

  return <Card title="服务器管理" extra={<Space wrap><Input allowClear placeholder="名称 / IP / SN / MAC" value={search} onChange={e=>setSearch(e.target.value)} style={{width:240}}/><Select allowClear placeholder="机房" value={dc} onChange={setDc} style={{width:160}} options={dcs.map(x=>({value:x.id,label:x.name}))}/>{canModify&&<Upload showUploadList={false} accept=".csv,.xlsx" beforeUpload={async f=>{try{const r=await importServers(f);message.success(`导入 ${r.created} 台，失败 ${r.failed} 条`);load()}catch{message.error('导入失败')}return false}}><Button icon={<CloudUploadOutlined/>}>Excel/CSV导入</Button></Upload>}{canModify&&<Button type="primary" icon={<PlusOutlined/>} onClick={openCreate}>新增服务器</Button>}</Space>}>
    <Alert showIcon type="info" style={{marginBottom:16}} message="资产、网卡、IP、VLAN 与物理连接统一管理。自动发现不会直接修改网络；连接需人工确认，配置需差异预览、预检查和审批。"/>
    <Space size={40} style={{marginBottom:16}} wrap><Statistic title="服务器资产" value={total}/><Statistic title="网卡" value={nics.length}/><Statistic title="IP地址" value={ipCount}/><Statistic title="连接关系" value={connections.length}/><Statistic title="待确认连接" value={connections.filter(x=>x.state==='candidate').length}/></Space>
    <Tabs activeKey={tab} onChange={switchTab} items={tabs}/>

    <Modal width={1000} title="新增服务器与网络信息" open={formOpen} onCancel={()=>setFormOpen(false)} onOk={async()=>{try{await createServerWithNetwork(await form.validateFields());setFormOpen(false);message.success('服务器、网卡和IP已完整录入');load()}catch{message.error('录入失败，请检查名称、MAC 和 IP 是否正确或重复')}}}>
      <Form form={form} layout="vertical">
        <h3>基础信息</h3><Space wrap align="start"><Form.Item name="name" label="服务器名称" rules={[{required:true}]}><Input style={{width:220}}/></Form.Item><Form.Item name="management_ip" label="管理IP"><Input style={{width:170}}/></Form.Item><Form.Item name="serial_number" label="序列号"><Input style={{width:180}}/></Form.Item><Form.Item name="vendor" label="厂商"><Input style={{width:140}}/></Form.Item><Form.Item name="model" label="型号"><Input style={{width:180}}/></Form.Item><Form.Item name="datacenter_id" label="机房"><Select allowClear style={{width:180}} options={dcs.map(x=>({value:x.id,label:x.name}))}/></Form.Item><Form.Item name="rack" label="机柜"><Input style={{width:120}}/></Form.Item><Form.Item name="status" label="状态"><Select style={{width:130}} options={Object.entries(statusText).map(([value,label])=>({value,label}))}/></Form.Item></Space>
        <h3>初始网卡、MAC、IP 与 VLAN <span style={{fontWeight:400,color:'#64748b',fontSize:13}}>（可稍后补录，也可一次添加多张）</span></h3>
        <Form.List name="nics">{(fields,{add,remove})=><Space direction="vertical" style={{width:'100%'}}>{fields.map(field=><Card size="small" key={field.key} title={`网卡 ${field.name+1}`} extra={<Button danger type="text" icon={<DeleteOutlined/>} onClick={()=>remove(field.name)}>删除</Button>}>
          <Space wrap align="start"><Form.Item name={[field.name,'name']} label="网卡名称" rules={[{required:true}]}><Input placeholder="eno1" style={{width:150}}/></Form.Item><Form.Item name={[field.name,'mac_address']} label="MAC地址" rules={[{required:true}]}><Input placeholder="00:11:22:33:44:55" style={{width:190}}/></Form.Item><Form.Item name={[field.name,'network_type']} label="网络用途" initialValue="business"><Select style={{width:130}} options={networkTypeOptions}/></Form.Item><Form.Item name={[field.name,'speed_mbps']} label="速率(Mbps)"><InputNumber min={0} style={{width:130}}/></Form.Item><Form.Item name={[field.name,'mtu']} label="MTU"><InputNumber min={576} max={65535} style={{width:100}}/></Form.Item><Form.Item name={[field.name,'bond_name']} label="Bond"><Input style={{width:120}}/></Form.Item></Space>
          <Form.List name={[field.name,'ip_addresses']}>{(ipFields,{add:addIp,remove:removeIp})=><><Space direction="vertical" style={{width:'100%'}}>{ipFields.map(ip=><Space key={ip.key} wrap align="start"><Form.Item name={[ip.name,'address']} label="IP地址" rules={[{required:true}]}><Input style={{width:170}}/></Form.Item><Form.Item name={[ip.name,'prefix_length']} label="前缀" initialValue={24}><InputNumber min={0} max={128} style={{width:85}}/></Form.Item><Form.Item name={[ip.name,'vlan_id']} label="VLAN"><InputNumber min={1} max={4094} style={{width:100}}/></Form.Item><Form.Item name={[ip.name,'network_type']} label="网络用途" initialValue="business"><Select style={{width:130}} options={networkTypeOptions}/></Form.Item><Button danger type="text" style={{marginTop:30}} onClick={()=>removeIp(ip.name)}>删除IP</Button></Space>)}</Space><Button size="small" icon={<PlusOutlined/>} onClick={()=>addIp({prefix_length:24,network_type:'business'})}>添加IP/VLAN</Button></>}</Form.List>
        </Card>)}<Button type="dashed" block icon={<PlusOutlined/>} onClick={()=>add({network_type:'business',status:'unknown',ip_addresses:[]})}>添加网卡</Button></Space>}</Form.List>
      </Form>
    </Modal>

    <Drawer width="88%" title={detail?`${detail.name} · 服务器详情`:'服务器详情'} open={!!detail} onClose={closeDetail} extra={canModify&&<Button icon={<PlusOutlined/>} onClick={()=>{nicForm.resetFields();nicForm.setFieldsValue({network_type:'business',status:'unknown'});setNicOpen(true)}}>添加网卡</Button>}>{detail&&<><Descriptions bordered size="small" column={4} items={[{key:'ip',label:'管理IP',children:detail.management_ip||'-'},{key:'sn',label:'序列号',children:detail.serial_number||'-'},{key:'model',label:'厂商/型号',children:`${detail.vendor||'-'} / ${detail.model||'-'}`},{key:'rack',label:'位置',children:`${detail.datacenter_name||'-'} / ${detail.rack||'-'}`},{key:'cpu',label:'CPU',children:detail.cpu_summary||'-'},{key:'memory',label:'内存',children:detail.memory_gb?`${detail.memory_gb} GB`:'-'},{key:'gpu',label:'GPU',children:detail.gpu_summary||'-'},{key:'bmc',label:'BMC',children:`${detail.bmc_type||'-'} / ${detail.bmc_address||'-'}`}]}/><h3>服务器网卡与 IP</h3><Table rowKey="id" pagination={false} dataSource={detail.nics||[]} columns={[{title:'网卡',dataIndex:'name'},{title:'MAC',dataIndex:'mac_address'},{title:'用途',dataIndex:'network_type',render:v=>networkTypeOptions.find(x=>x.value===v)?.label||v},{title:'速率 / MTU',render:(_,r:ServerNIC)=>`${r.speed_mbps||'-'} Mbps / ${r.mtu||'-'}`},{title:'IP / VLAN',render:(_,r:ServerNIC)=><Space wrap>{r.ip_addresses.map((x,i)=><Tag key={x.id||i}>{x.address}/{x.prefix_length}{x.vlan_id?` · VLAN ${x.vlan_id}`:''}</Tag>)}</Space>},{title:'操作',render:(_,r:ServerNIC)=>canModify&&<Button size="small" onClick={()=>{ipForm.resetFields();ipForm.setFieldsValue({prefix_length:32,network_type:r.network_type});setIpNic(r)}}>添加IP</Button>}]}/><h3>物理连接</h3><Table rowKey="id" dataSource={detailLinks} pagination={false} locale={{emptyText:'暂无候选或已确认的物理连接'}} columns={connectionColumns}/>{(detail.components||[]).length>0&&<><h3>硬件组件</h3><Table rowKey="id" pagination={false} dataSource={detail.components} columns={[{title:'类型',dataIndex:'component_type'},{title:'名称',dataIndex:'name'},{title:'厂商 / 型号',render:(_,r:any)=>`${r.vendor||'-'} / ${r.model||'-'}`},{title:'序列号',dataIndex:'serial_number',render:v=>v||'-'},{title:'健康状态',dataIndex:'health',render:v=><Tag color={v==='ok'||v==='healthy'?'green':v==='unknown'?'default':'red'}>{v}</Tag>},{title:'来源',dataIndex:'source'},{title:'最后发现',dataIndex:'last_discovered_at',render:v=>v?new Date(v).toLocaleString():'-'}]}/></>}</>}</Drawer>
    <Modal title="添加服务器网卡" open={nicOpen} onCancel={()=>setNicOpen(false)} onOk={async()=>{if(!detail)return;await addServerNIC(detail.id,await nicForm.validateFields());setNicOpen(false);message.success('网卡已添加');reload()}}><Form form={nicForm} layout="vertical"><Form.Item name="name" label="网卡名称" rules={[{required:true}]}><Input placeholder="eno1 / Mellanox0"/></Form.Item><Form.Item name="mac_address" label="MAC地址" rules={[{required:true}]}><Input/></Form.Item><Space wrap><Form.Item name="network_type" label="网络用途" rules={[{required:true}]}><Select style={{width:150}} options={networkTypeOptions}/></Form.Item><Form.Item name="speed_mbps" label="速率(Mbps)"><InputNumber min={0}/></Form.Item><Form.Item name="mtu" label="MTU"><InputNumber min={576} max={65535}/></Form.Item></Space><Form.Item name="bond_name" label="Bond / 聚合名称"><Input/></Form.Item></Form></Modal>
    <Modal title={`添加IP · ${ipNic?.name||''}`} open={!!ipNic} onCancel={()=>setIpNic(undefined)} onOk={async()=>{if(!ipNic)return;await addServerIP(ipNic.id,await ipForm.validateFields());setIpNic(undefined);message.success('IP已添加');reload()}}><Form form={ipForm} layout="vertical"><Form.Item name="address" label="IP地址" rules={[{required:true}]}><Input/></Form.Item><Space wrap><Form.Item name="prefix_length" label="前缀长度" rules={[{required:true}]}><InputNumber min={0} max={128}/></Form.Item><Form.Item name="vlan_id" label="VLAN ID"><InputNumber min={1} max={4094}/></Form.Item><Form.Item name="network_type" label="网络用途"><Select style={{width:150}} options={networkTypeOptions}/></Form.Item></Space></Form></Modal>
    <Modal title={`端口变更预检 · ${change?.switch_name||''} ${change?.switch_port||''}`} open={!!change} onCancel={()=>setChange(undefined)} onOk={async()=>{if(!change)return;const v=await changeForm.validateFields();const requested_config=Object.fromEntries(Object.entries(v).filter(([k,x])=>k!=='reason'&&x!==undefined&&x!==''));const r:any=await createPortChange({connection_id:change.id,requested_config,reason:v.reason});setChange(undefined);load();Modal.info({title:r.precheck_result?.passed?'预检查通过，等待审批':'预检查未通过',width:720,content:<pre style={{whiteSpace:'pre-wrap'}}>{JSON.stringify({差异:r.config_diff,预检查:r.precheck_result},null,2)}</pre>})}}><Alert showIcon type="warning" message="第一阶段只保存预检工单。交换机现有配置读取适配器验收前，不会下发任何配置。" style={{marginBottom:12}}/><Form form={changeForm} layout="vertical"><Form.Item name="reason" label="变更原因" rules={[{required:true}]}><Input.TextArea/></Form.Item><Space wrap><Form.Item name="mode" label="端口模式"><Select allowClear style={{width:140}} options={['access','trunk','hybrid'].map(value=>({value,label:value}))}/></Form.Item><Form.Item name="access_vlan" label="Access VLAN"><Input/></Form.Item><Form.Item name="allowed_vlans" label="允许VLAN"><Input placeholder="100,200-210"/></Form.Item><Form.Item name="mtu" label="MTU"><Input/></Form.Item><Form.Item name="aggregation_group" label="聚合组"><Input/></Form.Item></Space></Form></Modal>
  </Card>
}
