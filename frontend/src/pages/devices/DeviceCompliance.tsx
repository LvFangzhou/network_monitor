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
  ['snmp', 'SNMP'], ['syslog', 'Syslog'], ['tacacs', 'TACACS'],
  ['telemetry', 'Telemetry'], ['bmp', 'BMP'], ['nqa', 'NQA'],
  ['evpn_vxlan', 'EVPN/VXLAN'], ['roce', 'RoCE'], ['pfc', 'PFC'],
  ['ecn', 'ECN'], ['buffer', 'Buffer'], ['config_backup', '配置备份'],
].map(([value, label]) => ({ value, label }))

const CHECKS = [
  ['model_profile', '型号模板'], ['version', '版本'], ['patch', '补丁'],
  ['snmp', 'SNMP'], ['syslog', 'Syslog'], ['tacacs', 'TACACS'],
].map(([value, label]) => ({ value, label }))

const NETWORK_TYPES = [
  { value: 'general', label: '通用网络' },
  { value: 'oob', label: '带外网' },
  { value: 'management', label: '管理网' },
  { value: 'evpn_vxlan', label: 'EVPN/VXLAN' },
  { value: 'roce', label: 'RoCE网络' },
  { value: 'firewall', label: '防火墙网络' },
]

const DeviceCompliancePage = () => {
  const canModify = !useAuthStore((state) => state.user?.read_only)
  const [activeTab, setActiveTab] = useState('devices')
  const [loading, setLoading] = useState(false)
  const [evaluating, setEvaluating] = useState(false)
  const [profiles, setProfiles] = useState<DeviceModelProfile[]>([])
  const [baselines, setBaselines] = useState<VersionBaseline[]>([])
  const [devices, setDevices] = useState<DeviceCompliance[]>([])
  const [summary, setSummary] = useState({ total: 0, evaluated: 0, unevaluated: 0, counts: {} as Record<string, number>, compliance_rate: 0 })
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(50)
  const [statusFilter, setStatusFilter] = useState<string>()
  const [vendorFilter, setVendorFilter] = useState<string>()
  const [search, setSearch] = useState('')
  const [selected, setSelected] = useState<DeviceCompliance>()
  const [profileModal, setProfileModal] = useState(false)
  const [baselineModal, setBaselineModal] = useState(false)
  const [editingProfile, setEditingProfile] = useState<DeviceModelProfile>()
  const [editingBaseline, setEditingBaseline] = useState<VersionBaseline>()
  const [profileForm] = Form.useForm()
  const [baselineForm] = Form.useForm()

  const loadCatalogs = async () => {
    const [profileResult, baselineResult] = await Promise.all([getModelProfiles(), getVersionBaselines()])
    setProfiles(profileResult.items)
    setBaselines(baselineResult.items)
  }

  const loadDevices = async (refresh = false, nextPage = page, nextPageSize = pageSize) => {
    const result = await getComplianceDevices({
      skip: (nextPage - 1) * nextPageSize,
      limit: nextPageSize,
      overall_status: statusFilter,
      vendor: vendorFilter,
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
      required_checks: ['model_profile', 'version', 'snmp', 'syslog', 'tacacs'],
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

  const deviceColumns = [
    {
      title: '设备', key: 'device', width: 280,
      render: (_: unknown, record: DeviceCompliance) => (
        <Button type="link" style={{ padding: 0, height: 'auto', textAlign: 'left' }} onClick={() => setSelected(record)}>
          <div><strong>{record.device.name}</strong></div>
          <div style={{ color: '#7b8794', fontSize: 12 }}>{record.device.ip_address}</div>
        </Button>
      ),
    },
    { title: '机房', key: 'datacenter', width: 160, render: (_: unknown, record: DeviceCompliance) => record.device.datacenter?.name || '-' },
    { title: '厂商', key: 'vendor', width: 100, render: (_: unknown, record: DeviceCompliance) => record.device.vendor || '-' },
    { title: '型号', key: 'model', width: 180, render: (_: unknown, record: DeviceCompliance) => record.observed_model || record.device.model || '-' },
    { title: '软件版本', dataIndex: 'observed_version', key: 'version', width: 190, render: (value: string) => value || <Tag>待采集</Tag> },
    {
      title: '上线状态', dataIndex: 'overall_status', key: 'status', width: 120,
      render: (value: ComplianceStatus) => <Tag color={STATUS_META[value]?.color}>{STATUS_META[value]?.label || value}</Tag>,
    },
    {
      title: '合规度', dataIndex: 'score', key: 'score', width: 150,
      render: (value: number, record: DeviceCompliance) => (
        <Progress percent={value} size="small" status={record.overall_status === 'non_compliant' ? 'exception' : 'normal'} />
      ),
    },
    {
      title: '阻断项', key: 'blockers', width: 260,
      render: (_: unknown, record: DeviceCompliance) => record.blockers.length
        ? record.blockers.slice(0, 2).map((item) => <Tag key={item.key} color={item.status === 'failed' ? 'red' : 'orange'}>{item.label}</Tag>)
        : <Tag color="green">无</Tag>,
    },
    {
      title: '操作', key: 'action', fixed: 'right' as const, width: 130,
      render: (_: unknown, record: DeviceCompliance) => (
        <Space>
          <Button type="link" onClick={() => setSelected(record)}>清单</Button>
          {canModify && <Button type="link" onClick={() => void runEvaluation(record.device_id)}>核验</Button>}
        </Space>
      ),
    },
  ]

  const profileColumns = [
    { title: '模板名称', dataIndex: 'name', key: 'name', width: 180 },
    { title: '厂商', dataIndex: 'vendor', key: 'vendor', width: 100 },
    { title: '型号匹配', dataIndex: 'model_pattern', key: 'model_pattern', width: 180 },
    { title: '网络类型', dataIndex: 'network_type', key: 'network_type', width: 130 },
    {
      title: '能力', dataIndex: 'capabilities', key: 'capabilities',
      render: (value: Record<string, boolean>) => CAPABILITIES.filter((item) => value?.[item.value]).map((item) => <Tag key={item.value}>{item.label}</Tag>),
    },
    {
      title: '必检项', dataIndex: 'required_checks', key: 'required_checks', width: 260,
      render: (value: string[]) => CHECKS.filter((item) => value?.includes(item.value)).map((item) => <Tag key={item.value} color="blue">{item.label}</Tag>),
    },
    { title: '状态', dataIndex: 'is_active', key: 'is_active', width: 90, render: (value: boolean) => <Tag color={value ? 'green' : 'default'}>{value ? '启用' : '停用'}</Tag> },
    {
      title: '操作', key: 'action', width: 130,
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
    { title: '基线名称', dataIndex: 'name', key: 'name', width: 180 },
    {
      title: '适用型号模板', dataIndex: 'model_profile_id', key: 'profile', width: 180,
      render: (value: number) => profiles.find((item) => item.id === value)?.name || '按厂商/型号匹配',
    },
    { title: '厂商', dataIndex: 'vendor', key: 'vendor', width: 100, render: (value: string) => value || '-' },
    { title: '型号匹配', dataIndex: 'model_pattern', key: 'model', width: 160, render: (value: string) => value || '-' },
    { title: '允许版本', dataIndex: 'allowed_versions', key: 'allowed', render: (value: string[]) => value?.length ? value.map((item) => <Tag color="green" key={item}>{item}</Tag>) : '不限' },
    { title: '最低版本', dataIndex: 'minimum_version', key: 'minimum', width: 130, render: (value: string) => value || '-' },
    { title: '必需补丁', dataIndex: 'required_patches', key: 'patches', render: (value: string[]) => value?.length ? value.map((item) => <Tag color="blue" key={item}>{item}</Tag>) : '无' },
    { title: '禁用版本', dataIndex: 'forbidden_versions', key: 'forbidden', render: (value: string[]) => value?.length ? value.map((item) => <Tag color="red" key={item}>{item}</Tag>) : '无' },
    {
      title: '操作', key: 'action', width: 130,
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
                    onPressEnter={() => { setPage(1); void loadDevices(false, 1) }}
                    prefix={<SearchOutlined />} placeholder="设备名称、IP或型号" style={{ width: 260 }}
                  />
                  <Select allowClear placeholder="上线状态" value={statusFilter} onChange={setStatusFilter} style={{ width: 140 }}
                    options={Object.entries(STATUS_META).map(([value, meta]) => ({ value, label: meta.label }))} />
                  <Select allowClear showSearch placeholder="厂商" value={vendorFilter} onChange={setVendorFilter} style={{ width: 140 }} options={vendorOptions} />
                  <Button onClick={() => { setPage(1); void loadDevices(false, 1) }}>查询</Button>
                  <Button onClick={async () => {
                    setSearch(''); setStatusFilter(undefined); setVendorFilter(undefined); setPage(1)
                    const result = await getComplianceDevices({ skip: 0, limit: pageSize })
                    setDevices(result.items); setTotal(result.total)
                  }}>重置</Button>
                </Space>
                <Table
                  rowKey="device_id" loading={loading} dataSource={devices} columns={deviceColumns}
                  scroll={{ x: 1650 }}
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
                <Table rowKey="id" dataSource={profiles} columns={profileColumns} scroll={{ x: 1450 }} pagination={false} />
              </>
            ),
          },
          {
            key: 'baselines',
            label: '版本补丁管理',
            children: (
              <>
                {canModify && <Button type="primary" icon={<PlusOutlined />} onClick={() => openBaseline()} style={{ marginBottom: 12 }}>新增版本基线</Button>}
                <Table rowKey="id" dataSource={baselines} columns={baselineColumns} scroll={{ x: 1600 }} pagination={false} />
              </>
            ),
          },
        ]}
      />

      <Drawer title="设备上线检查清单" width={720} open={Boolean(selected)} onClose={() => setSelected(undefined)}
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
              <Descriptions.Item label="上线状态"><Tag color={STATUS_META[selected.overall_status]?.color}>{STATUS_META[selected.overall_status]?.label}</Tag></Descriptions.Item>
              <Descriptions.Item label="合规度"><Progress percent={selected.score} size="small" /></Descriptions.Item>
            </Descriptions>
            <Table
              rowKey="key" pagination={false} dataSource={selected.checks}
              columns={[
                { title: '检查项', dataIndex: 'label', key: 'label', width: 130 },
                {
                  title: '结果', dataIndex: 'status', key: 'status', width: 100,
                  render: (value: CheckStatus) => <Tag color={CHECK_META[value].color} icon={value === 'passed' ? <CheckCircleOutlined /> : value === 'failed' ? <CloseCircleOutlined /> : undefined}>{CHECK_META[value].label}</Tag>,
                },
                { title: '判断说明', dataIndex: 'message', key: 'message' },
                {
                  title: '证据', dataIndex: 'evidence', key: 'evidence', width: 220,
                  render: (value: any) => value ? <Tooltip title={<pre style={{ whiteSpace: 'pre-wrap' }}>{JSON.stringify(value, null, 2)}</pre>}><span>{typeof value === 'string' ? value : JSON.stringify(value)}</span></Tooltip> : '-',
                },
              ]}
            />
          </>
        )}
      </Drawer>

      <Modal title={`${editingProfile ? '编辑' : '新增'}型号能力模板`} width={760} open={profileModal} onCancel={() => setProfileModal(false)} onOk={() => void saveProfile()} destroyOnClose>
        <Form form={profileForm} layout="vertical">
          <Row gutter={12}>
            <Col span={12}><Form.Item name="name" label="模板名称" rules={[{ required: true }]}><Input placeholder="例如 H3C S9867 RoCE" /></Form.Item></Col>
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
            <Col span={12}><Form.Item name="model_profile_id" label="关联型号模板"><Select allowClear options={profiles.map((item) => ({ value: item.id, label: item.name }))} /></Form.Item></Col>
            <Col span={6}><Form.Item name="vendor" label="厂商"><Input /></Form.Item></Col>
            <Col span={6}><Form.Item name="model_pattern" label="型号匹配"><Input placeholder="支持 * 通配符" /></Form.Item></Col>
          </Row>
          <Form.Item name="device_role" label="设备角色"><Input placeholder="可选，Spine / Leaf等" /></Form.Item>
          <Form.Item
            name="allowed_versions"
            label="允许版本"
            extra="每个完整版本输入后按回车；版本中的逗号属于版本内容，不会再被拆分。"
          >
            <Select mode="tags" placeholder="例如：Software Version 7.1.070, Release 6715P01" />
          </Form.Item>
          <Row gutter={12}>
            <Col span={12}><Form.Item name="minimum_version" label="最低版本"><Input /></Form.Item></Col>
            <Col span={12}><Form.Item name="priority" label="匹配优先级"><InputNumber min={0} style={{ width: '100%' }} /></Form.Item></Col>
          </Row>
          <Form.Item name="required_patches" label="必需补丁" extra="只填写独立补丁名称；版本号中的P01等后缀不需要重复填在这里。">
            <Select mode="tags" placeholder="输入独立补丁名称后按回车；没有则留空" />
          </Form.Item>
          <Form.Item name="forbidden_versions" label="禁止版本">
            <Select mode="tags" placeholder="输入完整的已知故障版本后按回车" />
          </Form.Item>
          <Form.Item name="recommendation" label="整改建议"><Input.TextArea rows={3} /></Form.Item>
          <Form.Item name="is_active" label="启用" valuePropName="checked"><Switch /></Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}

export default DeviceCompliancePage
