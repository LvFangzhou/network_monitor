import { useEffect, useMemo, useState } from 'react'
import {
  Button, Card, Checkbox, Col, Descriptions, Drawer, Form, Input, InputNumber,
  Modal, Popconfirm, Progress, Row, Select, Space, Statistic, Switch, Table,
  Tabs, Tag, Tooltip, message,
} from 'antd'
import {
  CheckCircleOutlined, CloseCircleOutlined, EditOutlined, PlusOutlined,
  ReloadOutlined, SearchOutlined,
} from '@ant-design/icons'
import { useAuthStore } from '../../store/auth'
import {
  createModelProfile, createVersionBaseline, deleteModelProfile, deleteVersionBaseline, discoverModelProfiles,
  evaluateCompliance, getComplianceDevices, getComplianceSummary, getModelProfiles,
  getDeviceCompliance, getVersionBaselines, updateModelProfile, updateVersionBaseline,
} from '../../api/compliance'
import type {
  CheckStatus, ComplianceStatus, DeviceCompliance, DeviceModelProfile, VersionBaseline,
} from '../../api/compliance'
import { getDatacenters, type Datacenter } from '../../api/devices'


const STATUS_META: Record<ComplianceStatus, { label: string; color: string }> = {
  compliant: { label: '正确上线', color: 'success' },
  non_compliant: { label: '上线不合规', color: 'error' },
  pending: { label: '待核验', color: 'warning' },
  not_monitored: { label: '未纳管', color: 'default' },
}

const CHECK_META: Record<CheckStatus, { label: string; color: string }> = {
  passed: { label: '通过', color: 'success' },
  failed: { label: '不通过', color: 'error' },
  pending: { label: '待核验', color: 'warning' },
  skipped: { label: '不适用', color: 'default' },
}

const CAPABILITIES = [
  ['snmp', 'SNMP'], ['exporter', 'Exporter'], ['syslog', 'Syslog'], ['tacacs', 'TACACS'],
  ['telemetry', 'Telemetry'], ['bmp', 'BMP'], ['nqa', 'NQA'],
  ['evpn_vxlan', 'EVPN/VXLAN'], ['roce', 'RoCE'], ['pfc', 'PFC'],
  ['ecn', 'ECN'], ['buffer', 'Buffer'], ['config_backup', '配置备份'],
].map(([value, label]) => ({ value, label }))

const CHECKS = [
  ['model_profile', '型号模板'], ['device_name', '设备名称'], ['device_model', '设备型号'],
  ['serial_number', '序列号'], ['version', '版本'], ['patch', '补丁'],
  ['hardware', '硬件运行状态'],
  ['snmp', 'SNMP'], ['exporter', 'Exporter'], ['syslog', 'Syslog'], ['tacacs', 'TACACS'],
].map(([value, label]) => ({ value, label }))

const NETWORK_TYPES = [
  { value: 'general', label: '通用网络' },
  { value: 'oob', label: '带外网' },
  { value: 'management', label: '管理网' },
  { value: 'evpn_vxlan', label: 'EVPN/VXLAN' },
  { value: 'roce', label: 'RoCE网络' },
  { value: 'firewall', label: '防火墙网络' },
]

const normalizeVendor = (value?: string) => (value || '').trim().toLowerCase()

const isH3CVendor = (vendor?: string) => normalizeVendor(vendor).includes('h3c')
const isRuijieVendor = (vendor?: string) => {
  const normalized = normalizeVendor(vendor)
  return normalized.includes('ruijie') || normalized.includes('锐捷') || normalized.includes('rgos')
}
const useStructuredVersionFields = (vendor?: string) => isH3CVendor(vendor) || isRuijieVendor(vendor)

const vendorVersionLabel = (vendor?: string) => {
  const normalized = normalizeVendor(vendor)
  if (normalized.includes('ruijie') || normalized.includes('锐捷')) return '锐捷RGOS软件版本'
  if (normalized.includes('cisco') || normalized.includes('思科')) return 'Cisco NX-OS软件版本'
  if (normalized.includes('aster')) return 'AsterNOS软件版本'
  if (normalized.includes('hillstone') || normalized.includes('山石')) return '山石StoneOS软件版本'
  return '允许的软件版本'
}

const DeviceCompliancePage = () => {
  const canModify = !useAuthStore((state) => state.user?.read_only)
  const [activeTab, setActiveTab] = useState('devices')
  const [loading, setLoading] = useState(false)
  const [evaluating, setEvaluating] = useState(false)
  const [profiles, setProfiles] = useState<DeviceModelProfile[]>([])
  const [baselines, setBaselines] = useState<VersionBaseline[]>([])
  const [datacenters, setDatacenters] = useState<Datacenter[]>([])
  const [devices, setDevices] = useState<DeviceCompliance[]>([])
  const [summary, setSummary] = useState({ total: 0, evaluated: 0, unevaluated: 0, counts: {} as Record<string, number>, compliance_rate: 0 })
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)
  const [statusFilter, setStatusFilter] = useState<string>()
  const [vendorFilter, setVendorFilter] = useState<string>()
  const [datacenterFilter, setDatacenterFilter] = useState<number>()
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<DeviceCompliance>()
  const [profileModal, setProfileModal] = useState(false)
  const [baselineModal, setBaselineModal] = useState(false)
  const [editingProfile, setEditingProfile] = useState<DeviceModelProfile>()
  const [editingBaseline, setEditingBaseline] = useState<VersionBaseline>()
  const [profileForm] = Form.useForm()
  const [baselineForm] = Form.useForm()
  const baselineVendor = Form.useWatch('vendor', baselineForm)
  const isRuijieBaseline = isRuijieVendor(baselineVendor)
  const isStructuredBaseline = useStructuredVersionFields(baselineVendor)

  const loadCatalogs = async () => {
    const [profileResult, baselineResult, datacenterResult] = await Promise.all([getModelProfiles(), getVersionBaselines(), getDatacenters()])
    setProfiles(profileResult.items)
    setBaselines(baselineResult.items)
    setDatacenters(datacenterResult)
  }

  const loadDevices = async (refresh = false, nextPage = page, nextPageSize = pageSize) => {
    const result = await getComplianceDevices({
      skip: (nextPage - 1) * nextPageSize,
      limit: nextPageSize,
      overall_status: statusFilter,
      vendor: vendorFilter,
      datacenter_id: datacenterFilter,
      search: search.trim() || undefined,
      refresh,
    })
    setDevices(result.items)
    setTotal(result.total)
  }

  const loadAll = async (refresh = false) => {
    setLoading(true)
    try {
      await Promise.all([loadCatalogs(), loadDevices(refresh)])
      setSummary(await getComplianceSummary())
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '加载上线合规数据失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadAll()
  }, [])

  useEffect(() => {
    if (activeTab !== 'devices') return
    const timer = window.setTimeout(() => {
      setPage(1)
      void loadDevices(false, 1, pageSize)
    }, 300)
    return () => window.clearTimeout(timer)
  }, [activeTab, search, statusFilter, vendorFilter, datacenterFilter])

  const runEvaluation = async (deviceId?: number) => {
    setEvaluating(true)
    try {
      const result = await evaluateCompliance(deviceId)
      message.success(deviceId ? '该设备核验完成' : `已核验 ${result.total} 台设备`)
      await loadAll(false)
      if (deviceId) {
        setSelected(await getDeviceCompliance(deviceId))
      }
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '核验失败')
    } finally {
      setEvaluating(false)
    }
  }

  const openProfile = (record?: DeviceModelProfile) => {
    setEditingProfile(record)
    profileForm.resetFields()
    profileForm.setFieldsValue(record ? {
      ...record,
      capability_keys: Object.entries(record.capabilities || {}).filter(([, enabled]) => enabled).map(([key]) => key),
    } : {
      network_type: 'general',
      priority: 100,
      is_active: true,
      capability_keys: ['snmp', 'syslog', 'tacacs', 'config_backup'],
      required_checks: ['model_profile', 'version', 'hardware', 'snmp', 'syslog', 'tacacs'],
    })
    setProfileModal(true)
  }

  const saveProfile = async () => {
    try {
      const values = await profileForm.validateFields()
      const capabilityKeys: string[] = values.capability_keys || []
      const payload = {
        ...values,
        capabilities: Object.fromEntries(CAPABILITIES.map(({ value }) => [value, capabilityKeys.includes(value)])),
      }
      delete payload.capability_keys
      if (editingProfile) await updateModelProfile(editingProfile.id, payload)
      else await createModelProfile(payload)
      message.success('型号能力模板已保存')
      setProfileModal(false)
      await loadCatalogs()
    } catch (error: any) {
      if (!error?.errorFields) message.error(error?.response?.data?.detail || '保存失败')
    }
  }

  const openBaseline = (record?: VersionBaseline) => {
    setEditingBaseline(record)
    baselineForm.resetFields()
    baselineForm.setFieldsValue(record || { priority: 100, is_active: true })
    setBaselineModal(true)
  }

  const saveBaseline = async () => {
    try {
      const values = await baselineForm.validateFields()
      if (useStructuredVersionFields(values.vendor)) {
        values.allowed_versions = []
        values.minimum_version = null
      } else {
        values.platform_version = null
        values.allowed_releases = []
        values.minimum_version = null
      }
      if (editingBaseline) await updateVersionBaseline(editingBaseline.id, values)
      else await createVersionBaseline(values)
      message.success('版本补丁基线已保存')
      setBaselineModal(false)
      await loadCatalogs()
    } catch (error: any) {
      if (!error?.errorFields) message.error(error?.response?.data?.detail || '保存失败')
    }
  }

  const vendorOptions = useMemo(() => Array.from(new Set([
    ...profiles.map((item) => item.vendor),
    ...devices.map((item) => item.device.vendor || ''),
  ].filter(Boolean))).sort().map((value) => ({ value, label: value })), [profiles, devices])

  const datacenterOptions = useMemo(() => datacenters.map((item) => ({
    value: item.id,
    label: item.code ? `${item.name}（${item.code}）` : item.name,
  })), [datacenters])

  const deviceColumns = [
    {
      title: '设备名称', key: 'device', width: 240,
      render: (_: unknown, record: DeviceCompliance) => (
        <Button type="link" style={{ padding: 0, height: 'auto', textAlign: 'left' }} onClick={() => setSelected(record)}>
          <strong>{record.device.name}</strong>
        </Button>
      ),
    },
    { title: 'IP地址', key: 'ip_address', width: 130, ellipsis: true, render: (_: unknown, record: DeviceCompliance) => record.device.ip_address || '-' },
    { title: '机房', key: 'datacenter', width: 145, ellipsis: true, render: (_: unknown, record: DeviceCompliance) => record.device.datacenter?.name || '-' },
    { title: '厂商', key: 'vendor', width: 85, ellipsis: true, render: (_: unknown, record: DeviceCompliance) => record.device.vendor || '-' },
    { title: '型号', key: 'model', width: 145, ellipsis: true, render: (_: unknown, record: DeviceCompliance) => record.observed_model || record.device.model || '-' },
    { title: '软件版本', dataIndex: 'observed_version', key: 'version', width: 205, ellipsis: true, render: (value: string) => value || <Tag>待采集</Tag> },
    {
      title: '上线状态', dataIndex: 'overall_status', key: 'status', width: 105,
      render: (value: ComplianceStatus) => <Tag color={STATUS_META[value]?.color}>{STATUS_META[value]?.label || value}</Tag>,
    },
    {
      title: '合规度', dataIndex: 'score', key: 'score', width: 135,
      render: (value: number, record: DeviceCompliance) => (
        <Progress percent={value} size="small" status={record.overall_status === 'non_compliant' ? 'exception' : 'normal'} />
      ),
    },
    {
      title: '阻断项', key: 'blockers', width: 190,
      render: (_: unknown, record: DeviceCompliance) => record.blockers.length
        ? record.blockers.slice(0, 2).map((item) => <Tag key={item.key} color={item.status === 'failed' ? 'red' : 'orange'}>{item.label}</Tag>)
        : <Tag color="green">无</Tag>,
    },
    {
      title: '操作', key: 'action', fixed: 'right' as const, width: 110,
      render: (_: unknown, record: DeviceCompliance) => (
        <Space>
          <Button type="link" onClick={() => setSelected(record)}>清单</Button>
          {canModify && <Button type="link" onClick={() => void runEvaluation(record.device_id)}>核验</Button>}
        </Space>
      ),
    },
  ]

  const profileColumns = [
    { title: '模板名称', dataIndex: 'name', key: 'name', width: 145, ellipsis: true },
    { title: '厂商', dataIndex: 'vendor', key: 'vendor', width: 85, ellipsis: true },
    { title: '型号匹配', dataIndex: 'model_pattern', key: 'model_pattern', width: 145, ellipsis: true },
    { title: '网络类型', dataIndex: 'network_type', key: 'network_type', width: 110, ellipsis: true },
    {
      title: '能力', dataIndex: 'capabilities', key: 'capabilities', width: 330,
      render: (value: Record<string, boolean>) => CAPABILITIES.filter((item) => value?.[item.value]).map((item) => <Tag key={item.value}>{item.label}</Tag>),
    },
    {
      title: '必检项', dataIndex: 'required_checks', key: 'required_checks', width: 275,
      render: (value: string[]) => CHECKS.filter((item) => value?.includes(item.value)).map((item) => <Tag key={item.value} color="blue">{item.label}</Tag>),
    },
    { title: '状态', dataIndex: 'is_active', key: 'is_active', width: 75, render: (value: boolean) => <Tag color={value ? 'green' : 'default'}>{value ? '启用' : '停用'}</Tag> },
    {
      title: '操作', key: 'action', width: 105,
      render: (_: unknown, record: DeviceModelProfile) => canModify && (
        <Space>
          <Button type="link" icon={<EditOutlined />} onClick={() => openProfile(record)} />
          <Popconfirm title="删除该型号模板？" onConfirm={async () => { await deleteModelProfile(record.id); await loadCatalogs() }}>
            <Button type="link" danger>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  const baselineColumns = [
    { title: '基线名称', dataIndex: 'name', key: 'name', width: 155, ellipsis: true },
    {
      title: '适用型号模板', dataIndex: 'model_profile_id', key: 'profile', width: 155, ellipsis: true,
      render: (value: number) => profiles.find((item) => item.id === value)?.name || '按厂商/型号匹配',
    },
    { title: '厂商', dataIndex: 'vendor', key: 'vendor', width: 85, ellipsis: true, render: (value: string) => value || '-' },
    { title: '型号匹配', dataIndex: 'model_pattern', key: 'model', width: 135, ellipsis: true, render: (value: string) => value || '-' },
    {
      title: '版本要求', key: 'version_requirement', width: 320,
      render: (_: unknown, record: VersionBaseline) => useStructuredVersionFields(record.vendor) && (record.platform_version || record.allowed_releases?.length)
        ? (
          <Space size={[4, 4]} wrap>
            <Tag color="geekblue">{isRuijieVendor(record.vendor) ? '平台' : 'Comware'} {record.platform_version || '不限'}</Tag>
            {(record.allowed_releases || []).map((item) => <Tag color="green" key={item}>{isRuijieVendor(record.vendor) ? '设备版本' : 'Release'} {item}</Tag>)}
          </Space>
        )
        : record.allowed_versions?.length
          ? record.allowed_versions.map((item) => <Tag color="green" key={item}>{item}</Tag>)
          : '不限',
    },
    {
      title: '必需补丁', dataIndex: 'required_patches', key: 'patches', width: 190,
      render: (value: string[]) => value?.length
        ? value.map((item) => <Tag color="blue" key={item}>{item}</Tag>)
        : '无',
    },
    { title: '禁用版本', dataIndex: 'forbidden_versions', key: 'forbidden', width: 190, render: (value: string[]) => value?.length ? value.map((item) => <Tag color="red" key={item}>{item}</Tag>) : '无' },
    { title: '备注', dataIndex: 'recommendation', key: 'recommendation', width: 210, ellipsis: true, render: (value: string) => value || '-' },
    {
      title: '操作', key: 'action', width: 105,
      render: (_: unknown, record: VersionBaseline) => canModify && (
        <Space>
          <Button type="link" icon={<EditOutlined />} onClick={() => openBaseline(record)} />
          <Popconfirm title="删除该版本基线？" onConfirm={async () => { await deleteVersionBaseline(record.id); await loadCatalogs() }}>
            <Button type="link" danger>删除</Button>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <Card
      title="设备上线合规"
      extra={canModify && activeTab === 'devices' ? (
        <Button type="primary" icon={<ReloadOutlined />} loading={evaluating} onClick={() => void runEvaluation()}>
          全量重新核验
        </Button>
      ) : null}
    >
      <Row gutter={12} style={{ marginBottom: 16 }}>
        <Col span={4}><Card size="small"><Statistic title="设备总数" value={summary.total} /></Card></Col>
        <Col span={4}><Card size="small"><Statistic title="正确上线" value={summary.counts.compliant || 0} valueStyle={{ color: '#389e0d' }} /></Card></Col>
        <Col span={4}><Card size="small"><Statistic title="上线不合规" value={summary.counts.non_compliant || 0} valueStyle={{ color: '#cf1322' }} /></Card></Col>
        <Col span={4}><Card size="small"><Statistic title="待核验" value={(summary.counts.pending || 0) + summary.unevaluated} valueStyle={{ color: '#d48806' }} /></Card></Col>
        <Col span={4}><Card size="small"><Statistic title="未纳管" value={summary.counts.not_monitored || 0} /></Card></Col>
        <Col span={4}><Card size="small"><Statistic title="上线合规率" value={summary.compliance_rate} suffix="%" precision={2} /></Card></Col>
      </Row>

      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          {
            key: 'devices',
            label: '上线检查清单',
            children: (
              <>
                <Space wrap style={{ marginBottom: 12 }}>
                  <Input
                    allowClear value={search} onChange={(event) => setSearch(event.target.value)}
                    prefix={<SearchOutlined />} placeholder="设备名称、IP或型号" style={{ width: 260 }}
                  />
                  <Select allowClear placeholder="上线状态" value={statusFilter} onChange={setStatusFilter} style={{ width: 140 }}
                    options={Object.entries(STATUS_META).map(([value, meta]) => ({ value, label: meta.label }))} />
                  <Select allowClear showSearch placeholder="厂商" value={vendorFilter} onChange={setVendorFilter} style={{ width: 140 }} options={vendorOptions} />
                  <Select allowClear showSearch optionFilterProp="label" placeholder="机房" value={datacenterFilter} onChange={setDatacenterFilter} style={{ width: 220 }} options={datacenterOptions} />
                  <Button onClick={() => {
                    setSearch(''); setStatusFilter(undefined); setVendorFilter(undefined); setDatacenterFilter(undefined); setPage(1)
                  }}>重置</Button>
                </Space>
                <Table
                  rowKey="device_id" loading={loading} dataSource={devices} columns={deviceColumns}
                  size="small"
                  tableLayout="fixed"
                  scroll={{ x: 1490 }}
                  pagination={{
                    current: page, pageSize, total, showSizeChanger: true, showTotal: (value) => `共 ${value} 台`,
                    onChange: (nextPage, nextPageSize) => {
                      setPage(nextPage); setPageSize(nextPageSize)
                      void loadDevices(false, nextPage, nextPageSize)
                    },
                  }}
                />
              </>
            ),
          },
          {
            key: 'profiles',
            label: '型号能力管理',
            children: (
              <>
                {canModify && (
                  <Space style={{ marginBottom: 12 }}>
                    <Button type="primary" icon={<PlusOutlined />} onClick={() => openProfile()}>新增型号模板</Button>
                    <Button onClick={async () => {
                      const result = await discoverModelProfiles()
                      message.success(`已生成 ${result.created} 个型号模板，已有 ${result.skipped} 条设备记录未重复创建`)
                      await loadCatalogs()
                    }}>从现有设备生成</Button>
                  </Space>
                )}
                <Table rowKey="id" size="small" tableLayout="fixed" dataSource={profiles} columns={profileColumns} scroll={{ x: 1270 }} pagination={false} />
              </>
            ),
          },
          {
            key: 'baselines',
            label: '版本补丁管理',
            children: (
              <>
                {canModify && <Button type="primary" icon={<PlusOutlined />} onClick={() => openBaseline()} style={{ marginBottom: 12 }}>新增版本基线</Button>}
                <Table rowKey="id" size="small" tableLayout="fixed" dataSource={baselines} columns={baselineColumns} scroll={{ x: 1550 }} pagination={false} />
              </>
            ),
          },
        ]}
      />

      <Drawer title="设备上线检查清单" width={920} open={Boolean(selected)} onClose={() => setSelected(undefined)}
        extra={selected && canModify ? <Button loading={evaluating} onClick={() => void runEvaluation(selected.device_id)}>立即核验</Button> : null}>
        {selected && (
          <>
            <Descriptions bordered size="small" column={2} style={{ marginBottom: 16 }}>
              <Descriptions.Item label="设备">{selected.device.name}</Descriptions.Item>
              <Descriptions.Item label="管理地址">{selected.device.ip_address}</Descriptions.Item>
              <Descriptions.Item label="厂商/型号">{selected.observed_vendor || '-'} / {selected.observed_model || '-'}</Descriptions.Item>
              <Descriptions.Item label="软件版本">{selected.observed_version || '待采集'}</Descriptions.Item>
              <Descriptions.Item label="匹配模板">{selected.profile?.name || '未匹配'}</Descriptions.Item>
              <Descriptions.Item label="版本基线">{selected.baseline?.name || '未配置'}</Descriptions.Item>
              <Descriptions.Item label="已采集补丁" span={2}>
                {selected.observed_patches?.length ? (
                  <Space size={[4, 4]} wrap>
                    {selected.observed_patches.map((patch) => <Tag color="geekblue" key={patch}>{patch}</Tag>)}
                  </Space>
                ) : '未采集或无独立补丁'}
              </Descriptions.Item>
              <Descriptions.Item label="上线状态"><Tag color={STATUS_META[selected.overall_status]?.color}>{STATUS_META[selected.overall_status]?.label}</Tag></Descriptions.Item>
              <Descriptions.Item label="合规度"><Progress percent={selected.score} size="small" /></Descriptions.Item>
            </Descriptions>
            <Table
              rowKey="key" pagination={false} dataSource={selected.checks}
              tableLayout="fixed"
              scroll={{ x: 880 }}
              columns={[
                { title: '检查项', dataIndex: 'label', key: 'label', width: 150, ellipsis: true },
                {
                  title: '结果', dataIndex: 'status', key: 'status', width: 110,
                  render: (value: CheckStatus) => <Tag color={CHECK_META[value].color} icon={value === 'passed' ? <CheckCircleOutlined /> : value === 'failed' ? <CloseCircleOutlined /> : undefined}>{CHECK_META[value].label}</Tag>,
                },
                { title: '判断说明', dataIndex: 'message', key: 'message', width: 280, ellipsis: true },
                {
                  title: '原因', dataIndex: 'evidence', key: 'evidence', width: 340,
                  render: (value: any) => value ? (
                    <Tooltip title={<pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(value, null, 2)}</pre>}>
                      <span style={{ display: 'block', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                        {typeof value === 'string' ? value : JSON.stringify(value)}
                      </span>
                    </Tooltip>
                  ) : '-',
                },
              ]}
            />
          </>
        )}
      </Drawer>

      <Modal title={`${editingProfile ? '编辑' : '新增'}型号能力模板`} width={760} open={profileModal} onCancel={() => setProfileModal(false)} onOk={() => void saveProfile()} destroyOnClose>
        <Form form={profileForm} layout="vertical">
          <Row gutter={12}>
            <Col span={12}><Form.Item name="name" label="模板名称（仅型号）" rules={[{ required: true }]}><Input placeholder="例如 S9867-128DH" /></Form.Item></Col>
            <Col span={6}><Form.Item name="vendor" label="厂商" rules={[{ required: true }]}><Input placeholder="H3C" /></Form.Item></Col>
            <Col span={6}><Form.Item name="model_pattern" label="型号匹配" rules={[{ required: true }]}><Input placeholder="S9867*" /></Form.Item></Col>
          </Row>
          <Row gutter={12}>
            <Col span={8}><Form.Item name="network_type" label="网络类型"><Select options={NETWORK_TYPES} /></Form.Item></Col>
            <Col span={8}><Form.Item name="device_type" label="设备类型"><Input placeholder="Switch" /></Form.Item></Col>
            <Col span={8}><Form.Item name="default_role" label="默认角色"><Input placeholder="Spine / Leaf" /></Form.Item></Col>
          </Row>
          <Form.Item name="capability_keys" label="支持能力"><Checkbox.Group options={CAPABILITIES} /></Form.Item>
          <Form.Item name="required_checks" label="正确上线必检项"><Checkbox.Group options={CHECKS} /></Form.Item>
          <Row gutter={12}>
            <Col span={8}><Form.Item name="priority" label="匹配优先级"><InputNumber min={0} style={{ width: '100%' }} /></Form.Item></Col>
            <Col span={8}><Form.Item name="is_active" label="启用" valuePropName="checked"><Switch /></Form.Item></Col>
          </Row>
          <Form.Item name="description" label="说明"><Input.TextArea rows={3} /></Form.Item>
        </Form>
      </Modal>

      <Modal title={`${editingBaseline ? '编辑' : '新增'}版本补丁基线`} width={760} open={baselineModal} onCancel={() => setBaselineModal(false)} onOk={() => void saveBaseline()} destroyOnClose>
        <Form form={baselineForm} layout="vertical">
          <Form.Item name="name" label="基线名称" rules={[{ required: true }]}><Input placeholder="例如 H3C S9867 生产基线" /></Form.Item>
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item name="model_profile_id" label="关联型号模板">
                <Select
                  allowClear
                  showSearch
                  optionFilterProp="label"
                  options={profiles.map((item) => ({ value: item.id, label: item.name }))}
                  onChange={(profileId) => {
                    const profile = profiles.find((item) => item.id === profileId)
                    if (profile) baselineForm.setFieldsValue({ vendor: profile.vendor, model_pattern: profile.model_pattern })
                  }}
                />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="vendor" label="厂商" rules={[{ required: true, message: '请选择厂商' }]}>
                <Select allowClear showSearch optionFilterProp="label" options={vendorOptions} placeholder="选择厂商后显示版本字段" />
              </Form.Item>
            </Col>
            <Col span={6}><Form.Item name="model_pattern" label="型号匹配"><Input placeholder="支持 * 通配符" /></Form.Item></Col>
          </Row>
          <Form.Item name="device_role" label="设备角色"><Input placeholder="可选，Spine / Leaf等" /></Form.Item>
          {baselineVendor && isStructuredBaseline && (
            <Row gutter={12}>
              <Col span={10}>
                <Form.Item
                  name="platform_version"
                  label={isRuijieBaseline ? '锐捷平台版本' : 'H3C Comware平台版本'}
                  rules={[{ required: true, message: `请输入${isRuijieBaseline ? '锐捷平台版本' : 'Comware平台版本'}` }]}
                  extra={isRuijieBaseline ? '支持 * 和 ? 通配符；例如12.*可匹配12.5，11.*可匹配11.0。' : '支持 * 和 ? 通配符；例如7.1.*可匹配7.1.070、7.1.076等Comware 7.1平台。'}
                >
                  <Input placeholder={isRuijieBaseline ? '例如 11.0* 或 12.5*' : '精确值7.1.070，或通配值7.1.*'} />
                </Form.Item>
              </Col>
              <Col span={14}>
                <Form.Item
                  name="allowed_releases"
                  label={isRuijieBaseline ? '锐捷允许的设备版本' : 'H3C允许的Release软件版本'}
                  rules={[{ required: true, message: `请至少输入一个${isRuijieBaseline ? '设备版本' : 'Release软件版本'}` }]}
                  extra={isRuijieBaseline ? '填写设备实际软件版本；多个允许版本分别输入后按回车。' : '保留完整补丁后缀，例如6715P01；多个允许版本分别输入后按回车。'}
                >
                  <Select mode="tags" placeholder={isRuijieBaseline ? '例如 12.5(2)B0605 或 11.0(5)B9P62' : '例如 6715P01'} />
                </Form.Item>
              </Col>
            </Row>
          )}
          {baselineVendor && isStructuredBaseline && (
            <Form.Item
              name="required_patches"
              label={isRuijieBaseline ? '锐捷必需补丁' : 'H3C必需补丁'}
              extra={isRuijieBaseline ? '仅在锐捷设备能够采集到独立补丁信息时填写；多个补丁分别输入后按回车。未要求补丁时留空。' : '从设备hh3cSysPackageTable自动读取并比较；多个必需补丁分别输入后按回车。未要求补丁时留空。'}
            >
              <Select mode="tags" placeholder={isRuijieBaseline ? '输入补丁名称后按回车' : '例如 R6715HS09'} />
            </Form.Item>
          )}
          {baselineVendor && !isStructuredBaseline && (
            <>
              <Form.Item
                name="allowed_versions"
                label={vendorVersionLabel(baselineVendor)}
                rules={[{ required: true, message: '请至少输入一个允许的软件版本' }]}
                extra="每个完整软件版本输入后按回车，支持输入多个允许版本。"
              >
                <Select mode="tags" placeholder={`输入${vendorVersionLabel(baselineVendor)}后按回车`} />
              </Form.Item>
              <Form.Item name="required_patches" label="必需独立补丁" extra="仅在厂商能够单独采集补丁列表时填写；没有则留空。">
                <Select mode="tags" placeholder="输入独立补丁名称后按回车" />
              </Form.Item>
            </>
          )}
          <Form.Item name="forbidden_versions" label="禁止版本">
            <Select mode="tags" placeholder={isStructuredBaseline ? `输入禁止的${isRuijieBaseline ? '设备版本' : 'Release版本'}后按回车` : '输入完整的已知故障版本后按回车'} />
          </Form.Item>
          <Form.Item name="recommendation" label="备注"><Input.TextArea rows={3} /></Form.Item>
          <Row gutter={12}>
            <Col span={12}><Form.Item name="priority" label="匹配优先级"><InputNumber min={0} style={{ width: '100%' }} /></Form.Item></Col>
            <Col span={12}><Form.Item name="is_active" label="启用" valuePropName="checked"><Switch /></Form.Item></Col>
          </Row>
        </Form>
      </Modal>
    </Card>
  )
}

export default DeviceCompliancePage
