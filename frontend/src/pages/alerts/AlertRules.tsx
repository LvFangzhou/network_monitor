import { useEffect, useRef, useState } from 'react'
import {
  Button,
  Card,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Radio,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  message,
} from 'antd'
import { DeleteOutlined, EditOutlined, EyeOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import {
  createAlertRule,
  deleteAlertRule,
  getAlertRuleStatus,
  getAlertRules,
  testAlertNotification,
  updateAlertRule,
  type AlertRule,
  type AlertRuleStatusItem,
  type AlertRuleStatusResponse,
  type AlertRulePayload,
} from '../../api/alerts'
import { getDevices, type Device } from '../../api/devices'
import { useAuthStore } from '../../store/auth'

const metricOptions = [
  { value: 'snmp_cpu', label: 'CPU 使用率' },
  { value: 'snmp_memory', label: '内存使用率' },
  { value: 'device_temperature', label: '设备温度' },
  { value: 'snmp_session_usage', label: '会话使用率' },
  { value: 'snmp_session_queue_full_drop_delta', label: '会话队列满丢包增长' },
  { value: 'snmp_storage_usage', label: '存储使用率' },
  { value: 'snmp_fan_status', label: '风扇状态' },
  { value: 'snmp_power_status', label: '电源状态' },
  { value: 'snmp_ha_status', label: 'HA 状态' },
  { value: 'snmp_pak_buffer_usage', label: 'Packet Buffer 使用率' },
  { value: 'snmp_ipsec_tunnel_status', label: 'IPSec 隧道状态' },
  { value: 'snmp_snat_resource_usage', label: 'SNAT 资源使用率' },
  { value: 'snmp_dnat_server_status', label: 'DNAT 服务器状态' },
  { value: 'snmp_slb_virtual_server_status', label: 'SLB 虚拟服务状态' },
  { value: 'device_status', label: '设备状态' },
  { value: 'device_reachability', label: '设备可达状态（30秒 ICMP / 5 包）' },
  { value: 'interface_oper_status', label: '接口 up/down 状态' },
  { value: 'interface_admin_up_oper_down', label: '接口 admin up 但物理 down' },
  { value: 'interface_in_errors_delta', label: '接口入错包增量' },
  { value: 'interface_out_errors_delta', label: '接口出错包增量' },
  { value: 'interface_in_discards_delta', label: '接口入丢包增量' },
  { value: 'interface_out_discards_delta', label: '接口出丢包增量' },
  { value: 'interface_in_broadcast_pps', label: '接口入广播包速率' },
  { value: 'interface_out_broadcast_pps', label: '接口出广播包速率' },
  { value: 'bgp_peer_state', label: 'BGP 邻居状态' },
  { value: 'ospf_neighbor_state', label: 'OSPF 邻居状态' },
  { value: 'bfd_session_state', label: 'BFD 会话状态（需私有 MIB）' },
  { value: 'optical_rx_power', label: '收光功率（需私有 MIB）' },
  { value: 'optical_tx_power', label: '发光功率（需私有 MIB）' },
  { value: 'internet_circuit_traffic_floor', label: '公网线路流量掉底' },
  { value: 'private_line_circuit_traffic_floor', label: '专线流量掉底' },
  { value: 'syslog_keyword', label: 'Syslog 关键字匹配' },
]

const conditionOptions = ['>', '>=', '<', '<=', '==', '!='].map((value) => ({
  value,
  label: value,
}))

const severityColors: Record<string, string> = {
  P0: 'red',
  P1: 'gold',
  P2: 'blue',
  P3: 'default',
  critical: 'red',
  warning: 'gold',
  info: 'blue',
}

const severityLabels: Record<string, string> = {
  P0: 'P0',
  P1: 'P1',
  P2: 'P2',
  P3: 'P3',
  critical: 'P0',
  warning: 'P1',
  info: 'P2',
}

const detectWebhookChannel = (url?: string) => {
  const normalized = (url || '').trim().toLowerCase()
  if (!normalized) {
    return { type: 'webhook' as const, label: '未填写' }
  }
  if (normalized.includes('work.weixin.qq.com') || normalized.includes('qyapi.weixin.qq.com')) {
    return { type: 'wechat' as const, label: '企业微信机器人' }
  }
  if (normalized.includes('oapi.dingtalk.com') || normalized.includes('api.dingtalk.com')) {
    return { type: 'dingtalk' as const, label: '钉钉机器人' }
  }
  if (normalized.includes('open.feishu.cn') || normalized.includes('open.larksuite.com')) {
    return { type: 'feishu' as const, label: '飞书机器人' }
  }
  return { type: 'webhook' as const, label: '通用 Webhook' }
}

const normalizeSeverityValue = (value?: string | null) => severityLabels[value || ''] || 'P1'

const AlertRules = () => {
  const [rules, setRules] = useState<AlertRule[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(20)
  const [searchText, setSearchText] = useState('')
  const [severityFilter, setSeverityFilter] = useState<string | null>(null)
  const [enabledFilter, setEnabledFilter] = useState<boolean | null>(null)
  const [loading, setLoading] = useState(false)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [editingRule, setEditingRule] = useState<AlertRule | null>(null)
  const [detectedWebhookLabel, setDetectedWebhookLabel] = useState('未填写')
  const [testingWebhook, setTestingWebhook] = useState(false)
  const [deviceOptions, setDeviceOptions] = useState<Device[]>([])
  const [loadingDevices, setLoadingDevices] = useState(false)
  const [deviceKeyword, setDeviceKeyword] = useState('')
  const [statusModalOpen, setStatusModalOpen] = useState(false)
  const [statusRule, setStatusRule] = useState<AlertRule | null>(null)
  const [ruleStatus, setRuleStatus] = useState<AlertRuleStatusResponse | null>(null)
  const [loadingRuleStatus, setLoadingRuleStatus] = useState(false)
  const [ruleStatusFilter, setRuleStatusFilter] = useState<'normal' | 'alert' | 'no_data' | undefined>()
  const [ruleStatusSearch, setRuleStatusSearch] = useState('')
  const ruleStatusCacheRef = useRef(new Map<string, { data: AlertRuleStatusResponse; cachedAt: number }>())
  const [form] = Form.useForm()
  const canModify = !useAuthStore((state) => state.user?.read_only)

  const buildRuleStatusCacheKey = (
    ruleId: number,
    search?: string,
    status?: 'normal' | 'alert' | 'no_data'
  ) => `${ruleId}:${(search || '').trim().toLowerCase()}:${status || ''}`

  const fetchRules = async (
    nextPage = page,
    nextPageSize = pageSize,
    keyword = searchText,
    severity = severityFilter,
    enabled = enabledFilter
  ) => {
    setLoading(true)
    try {
      const result = await getAlertRules({
        skip: (nextPage - 1) * nextPageSize,
        limit: nextPageSize,
        search: keyword.trim() || undefined,
        severity: severity || undefined,
        enabled: enabled === null ? undefined : enabled,
      })
      setRules(result.items)
      setTotal(result.total)
      setPage(nextPage)
      setPageSize(nextPageSize)
      setSeverityFilter(severity)
      setEnabledFilter(enabled)
    } catch (error) {
      message.error('获取告警规则失败')
    } finally {
      setLoading(false)
    }
  }

  const fetchRuleStatus = async (
    rule: AlertRule,
    nextSearch = ruleStatusSearch,
    nextStatus = ruleStatusFilter,
    forceRefresh = false
  ) => {
    const cacheKey = buildRuleStatusCacheKey(rule.id, nextSearch, nextStatus)
    const cached = ruleStatusCacheRef.current.get(cacheKey)
    if (!forceRefresh && cached && Date.now() - cached.cachedAt < 30_000) {
      setRuleStatus({ ...cached.data, cached: true })
      return
    }
    setLoadingRuleStatus(true)
    try {
      const result = await getAlertRuleStatus(rule.id, {
        search: nextSearch.trim() || undefined,
        status: nextStatus,
        limit: 1000,
        refresh: forceRefresh || undefined,
      })
      ruleStatusCacheRef.current.set(cacheKey, { data: result, cachedAt: Date.now() })
      setRuleStatus(result)
    } catch (error) {
      message.error('获取规则当前状态失败')
    } finally {
      setLoadingRuleStatus(false)
    }
  }

  useEffect(() => {
    fetchRules(1, pageSize, searchText, severityFilter, enabledFilter)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      fetchRules(1, pageSize, searchText, severityFilter, enabledFilter)
    }, 300)
    return () => window.clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchText, severityFilter, enabledFilter])

  const fetchDeviceOptions = async (keyword = deviceKeyword) => {
    setLoadingDevices(true)
    try {
      const result = await getDevices({
        limit: 100,
        status: 'active',
        is_monitored: true,
        search: keyword.trim() || undefined,
      })
      setDeviceOptions(result.items || [])
    } catch (error) {
      message.error('获取适用设备列表失败')
    } finally {
      setLoadingDevices(false)
    }
  }

  useEffect(() => {
    if (!drawerOpen) return
    const timer = window.setTimeout(() => {
      fetchDeviceOptions(deviceKeyword)
    }, 300)
    return () => window.clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [drawerOpen, deviceKeyword])

  const handleSeverityChange = (value?: string | null) => {
    setSeverityFilter(value || null)
  }

  const handleEnabledChange = (value?: boolean | null) => {
    setEnabledFilter(value ?? null)
  }

  const handleResetFilters = () => {
    setSearchText('')
    setSeverityFilter(null)
    setEnabledFilter(null)
    fetchRules(1, pageSize, '', null, null)
  }

  const openCreate = () => {
    setEditingRule(null)
    form.resetFields()
    setDeviceKeyword('')
    form.setFieldsValue({
      rule_type: 'threshold',
      metric_type: 'snmp_cpu',
      condition: '>',
      threshold: 80,
      duration: 60,
      suppress_duration: 300,
      severity: 'P1',
      enabled: true,
      scope_type: 'all',
      device_ids: [],
      mention_users_text: '',
      extra_config_text: '',
      notification_channels: [],
    })
    setDetectedWebhookLabel('未填写')
    setDrawerOpen(true)
  }

  const openEdit = (rule: AlertRule) => {
    const existingWebhookChannel = rule.notification_channels?.find((channel) =>
      ['webhook', 'wechat', 'dingtalk', 'feishu'].includes(channel.type)
    )
    const existingWebhookUrl =
      existingWebhookChannel?.config?.url ||
      existingWebhookChannel?.config?.webhook ||
      ''
    setEditingRule(rule)
    setDeviceKeyword('')
    form.setFieldsValue({
      ...rule,
      severity: normalizeSeverityValue(rule.severity),
      scope_type: rule.device_ids?.length ? 'specific' : 'all',
      device_ids: rule.device_ids || [],
      mention_users_text: Array.isArray(rule.extra_config?.mention_users)
        ? rule.extra_config?.mention_users.join(', ')
        : '',
      extra_config_text: JSON.stringify(rule.extra_config || {}, null, 2),
      webhook_url: existingWebhookUrl,
      notification_channels: rule.notification_channels || [],
    })
    setDetectedWebhookLabel(detectWebhookChannel(existingWebhookUrl).label)
    setDrawerOpen(true)
  }

  const openRuleStatus = (rule: AlertRule) => {
    setStatusRule(rule)
    setRuleStatus(null)
    setRuleStatusSearch('')
    setRuleStatusFilter(undefined)
    setStatusModalOpen(true)
    fetchRuleStatus(rule, '', undefined)
  }

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields()
      const detectedWebhook = detectWebhookChannel(values.webhook_url)
      const extraConfig = values.extra_config_text ? JSON.parse(values.extra_config_text) : {}
      const mentionUsers = String(values.mention_users_text || '')
        .split(',')
        .map((item: string) => item.trim())
        .filter(Boolean)
      extraConfig.mention_users = mentionUsers
      const payload: AlertRulePayload = {
        name: values.name,
        description: values.description,
        rule_type: values.rule_type,
        metric_type: values.metric_type,
        condition: values.condition,
        threshold: values.threshold,
        duration: values.duration,
        suppress_duration: values.suppress_duration,
        severity: values.severity,
        enabled: values.enabled,
        device_group_id: values.device_group_id,
        device_ids: values.scope_type === 'specific' ? values.device_ids || [] : [],
        extra_config: extraConfig,
        notification_channels: values.webhook_url
          ? [
              {
                type: detectedWebhook.type,
                config: {
                  ...(detectedWebhook.type === 'webhook'
                    ? { url: values.webhook_url }
                    : { webhook: values.webhook_url }),
                  mention_users: mentionUsers,
                },
              },
            ]
          : [],
      }

      if (editingRule) {
        await updateAlertRule(editingRule.id, payload)
        message.success('规则已更新')
      } else {
        await createAlertRule(payload)
        message.success('规则已创建')
      }

      setDrawerOpen(false)
      fetchRules()
    } catch (error: any) {
      if (!error?.errorFields) {
        message.error(error?.response?.data?.detail || '保存失败')
      }
    }
  }

  const handleDelete = async (id: number) => {
    try {
      await deleteAlertRule(id)
      message.success('规则已删除')
      fetchRules()
    } catch (error) {
      message.error('删除失败')
    }
  }

  const handleTestWebhook = async () => {
    const webhookUrl = form.getFieldValue('webhook_url')
    if (!webhookUrl) {
      message.warning('请先填写机器人地址')
      return
    }

    try {
      await form.validateFields(['webhook_url'])
      setTestingWebhook(true)
      const result = await testAlertNotification(webhookUrl)
      message.success(result.message)
    } catch (error: any) {
      if (!error?.errorFields) {
        message.error(error?.response?.data?.detail || '测试发送失败')
      }
    } finally {
      setTestingWebhook(false)
    }
  }

  const applyMetricDefaults = (metricType?: string) => {
    if (metricType === 'device_reachability') {
      form.setFieldsValue({
        condition: '<',
        threshold: 1,
        duration: Math.max(Number(form.getFieldValue('duration') || 0), 60),
        suppress_duration: Math.max(Number(form.getFieldValue('suppress_duration') || 0), 300),
        severity: form.getFieldValue('severity') || 'P1',
      })
    }
  }

  const renderRuleStatusTag = (value: AlertRuleStatusItem['status']) => {
    if (value === 'alert') return <Tag color="red">异常</Tag>
    if (value === 'normal') return <Tag color="green">正常</Tag>
    return <Tag color="default">无数据</Tag>
  }

  return (
    <Card
      title="告警规则"
      extra={
        <Space>
          <Input.Search
            allowClear
            placeholder="搜索规则名称"
            value={searchText}
            onChange={(event) => setSearchText(event.target.value)}
            style={{ width: 280 }}
          />
          <Select
            allowClear
            placeholder="使用状态"
            value={enabledFilter ?? undefined}
            onChange={handleEnabledChange}
            style={{ width: 120 }}
            options={[
              { value: true, label: '启用' },
              { value: false, label: '停用' },
            ]}
          />
          <Select
            allowClear
            placeholder="告警级别"
            value={severityFilter ?? undefined}
            onChange={handleSeverityChange}
            style={{ width: 120 }}
            options={[
              { value: 'P0', label: 'P0' },
              { value: 'P1', label: 'P1' },
              { value: 'P2', label: 'P2' },
              { value: 'P3', label: 'P3' },
            ]}
          />
          <Tooltip title="重置筛选">
            <Button icon={<ReloadOutlined />} onClick={handleResetFilters}>
              重置
            </Button>
          </Tooltip>
          {canModify ? (
            <Tooltip title="新建规则">
              <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
                新建规则
              </Button>
            </Tooltip>
          ) : null}
        </Space>
      }
    >
      <Table
        rowKey="id"
        loading={loading}
        dataSource={rules}
        pagination={{
          current: page,
          pageSize,
          total,
          showSizeChanger: true,
          pageSizeOptions: [10, 20, 50, 100],
          showTotal: (count, range) => `第 ${range[0]}-${range[1]} 条 / 共 ${count} 条`,
	          onChange: (nextPage, nextPageSize) => {
	            fetchRules(nextPage, nextPageSize, searchText, severityFilter, enabledFilter)
	          },
        }}
        columns={[
          {
            title: '规则名称',
            dataIndex: 'name',
            render: (value: string, record: AlertRule) => (
              <Space direction="vertical" size={0}>
                <span style={{ fontWeight: 500 }}>{value}</span>
                <span style={{ color: '#666', fontSize: 12 }}>{record.description || '无描述'}</span>
              </Space>
            ),
          },
          {
            title: '指标',
            dataIndex: 'metric_type',
          },
          {
            title: '条件',
            render: (_: unknown, record: AlertRule) => `${record.condition} ${record.threshold}`,
          },
          {
            title: '持续时间',
            dataIndex: 'duration',
            render: (value: number) => `${value}s`,
          },
          {
            title: '级别',
            dataIndex: 'severity',
            render: (value: string) => <Tag color={severityColors[value] || 'default'}>{severityLabels[value] || value}</Tag>,
          },
          {
            title: '重复告警间隔',
            dataIndex: 'suppress_duration',
            render: (value?: number) => `${value || 0}s`,
          },
          {
            title: '状态',
            dataIndex: 'enabled',
            render: (value: boolean) => <Tag color={value ? 'green' : 'default'}>{value ? '启用' : '停用'}</Tag>,
          },
          {
            title: '操作',
            width: 150,
            render: (_: unknown, record: AlertRule) => (
              <Space>
                <Tooltip title="查看状态">
                  <Button type="text" icon={<EyeOutlined />} onClick={() => openRuleStatus(record)} />
                </Tooltip>
                {canModify ? (
                  <>
                    <Tooltip title="编辑">
                      <Button type="text" icon={<EditOutlined />} onClick={() => openEdit(record)} />
                    </Tooltip>
                    <Popconfirm
                      title="确认删除这条规则吗？"
                      onConfirm={() => handleDelete(record.id)}
                      okText="删除"
                      cancelText="取消"
                    >
                      <Tooltip title="删除">
                        <Button type="text" danger icon={<DeleteOutlined />} />
                      </Tooltip>
                    </Popconfirm>
                  </>
                ) : null}
              </Space>
            ),
          },
        ]}
      />

      <Modal
        title={statusRule ? `规则当前状态 / ${statusRule.name}` : '规则当前状态'}
        open={statusModalOpen}
        onCancel={() => setStatusModalOpen(false)}
        footer={null}
        width={1100}
        destroyOnClose
      >
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Space wrap>
            <Tag color="blue">{`对象 ${ruleStatus?.summary.total ?? 0}`}</Tag>
            <Tag color="green">{`正常 ${ruleStatus?.summary.normal ?? 0}`}</Tag>
            <Tag color="red">{`异常 ${ruleStatus?.summary.alert ?? 0}`}</Tag>
            <Tag>{`无数据 ${ruleStatus?.summary.no_data ?? 0}`}</Tag>
            {ruleStatus?.truncated ? <Tag color="gold">结果已截断</Tag> : null}
            {ruleStatus?.cached ? <Tag color="cyan">缓存</Tag> : null}
          </Space>
          <Space wrap>
            <Input.Search
              allowClear
              placeholder="搜索设备名称/IP/型号"
              value={ruleStatusSearch}
              onChange={(event) => setRuleStatusSearch(event.target.value)}
              onSearch={(value) => {
                if (statusRule) fetchRuleStatus(statusRule, value, ruleStatusFilter)
              }}
              style={{ width: 260 }}
            />
            <Select
              allowClear
              placeholder="状态筛选"
              value={ruleStatusFilter}
              options={[
                { value: 'normal', label: '正常' },
                { value: 'alert', label: '异常' },
                { value: 'no_data', label: '无数据' },
              ]}
              onChange={(value) => {
                setRuleStatusFilter(value)
                if (statusRule) fetchRuleStatus(statusRule, ruleStatusSearch, value)
              }}
              style={{ width: 140 }}
            />
            <Button
              icon={<ReloadOutlined />}
              onClick={() => {
                if (statusRule) fetchRuleStatus(statusRule, ruleStatusSearch, ruleStatusFilter, true)
              }}
            >
              刷新
            </Button>
          </Space>
          <Table<AlertRuleStatusItem>
            rowKey={(record) => `${record.device_id}:${record.target_key || record.target_name || 'device'}`}
            size="small"
            loading={loadingRuleStatus}
            dataSource={ruleStatus?.items || []}
            pagination={{ defaultPageSize: 10, showSizeChanger: true, pageSizeOptions: [10, 20, 50, 100] }}
            columns={[
              {
                title: '设备',
                render: (_: unknown, record) => (
                  <Space direction="vertical" size={0}>
                    <span>{record.device_name || '-'}</span>
                    <span style={{ color: '#666', fontSize: 12 }}>{record.device_ip}</span>
                  </Space>
                ),
              },
              {
                title: '对象',
                dataIndex: 'target_name',
                render: (value: string | null, record) => value || record.target_key || '-',
              },
              {
                title: '当前值',
                dataIndex: 'value',
                render: (value?: number | null) => value ?? '-',
              },
              {
                title: '条件',
                dataIndex: 'condition',
              },
              {
                title: '状态',
                dataIndex: 'status',
                render: renderRuleStatusTag,
              },
              {
                title: '补充',
                render: (_: unknown, record) => record.state_text || record.message || '-',
              },
            ]}
          />
        </Space>
      </Modal>

      <Drawer
        title={editingRule ? '编辑告警规则' : '新建告警规则'}
        width={520}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        extra={
          <Space>
            <Button onClick={() => setDrawerOpen(false)}>取消</Button>
            <Button type="primary" onClick={handleSubmit}>
              保存
            </Button>
          </Space>
        }
      >
        <Form
          form={form}
          layout="vertical"
          onValuesChange={(changedValues) => {
            if (Object.prototype.hasOwnProperty.call(changedValues, 'metric_type')) {
              applyMetricDefaults(changedValues.metric_type)
            }
          }}
        >
          <Form.Item name="name" label="规则名称" rules={[{ required: true, message: '请输入规则名称' }]}>
            <Input placeholder="例如：CPU 持续高负载" />
          </Form.Item>
          <Form.Item name="description" label="说明">
            <Input.TextArea rows={3} placeholder="规则说明" />
          </Form.Item>
          <Form.Item
            name="metric_type"
            label="监控指标"
            extra={form.getFieldValue('metric_type') === 'device_reachability' ? '设备可达状态：1=可达，0=不可达。推荐条件填写为 “< 1”。' : undefined}
            rules={[{ required: true, message: '请选择指标' }]}
          >
            <Select options={metricOptions} />
          </Form.Item>
          <Form.Item
            name="scope_type"
            label="适用范围"
            extra="默认适用于所有已加入监控、状态为上线/活跃的设备；新设备加入监控后会自动纳入这些规则。"
          >
            <Radio.Group
              options={[
                { value: 'all', label: '全部监控设备' },
                { value: 'specific', label: '指定设备' },
              ]}
            />
          </Form.Item>
          <Form.Item
            noStyle
            shouldUpdate={(prevValues, nextValues) => prevValues.scope_type !== nextValues.scope_type}
          >
            {({ getFieldValue }) =>
              getFieldValue('scope_type') === 'specific' ? (
                <Form.Item
                  name="device_ids"
                  label="指定设备"
                  extra="只有确实需要让某条规则只检查少数设备时才填写；可搜索 IP、名称或设备 ID。"
                >
                  <Select
                    mode="multiple"
                    allowClear
                    showSearch
                    filterOption={false}
                    loading={loadingDevices}
                    placeholder="例如搜索 10.242.2.30 或 164"
                    onSearch={setDeviceKeyword}
                    optionFilterProp="label"
                    options={deviceOptions.map((device) => ({
                      value: device.id,
                      label: `${device.ip_address} - ${device.name || device.hostname || '未命名'} (#${device.id})`,
                    }))}
                  />
                </Form.Item>
              ) : null
            }
          </Form.Item>
          <Form.Item name="condition" label="条件" rules={[{ required: true, message: '请选择条件' }]}>
            <Select options={conditionOptions} />
          </Form.Item>
          <Form.Item name="threshold" label="阈值" rules={[{ required: true, message: '请输入阈值' }]}>
            <InputNumber min={0} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="duration" label="持续时间(秒)" rules={[{ required: true, message: '请输入持续时间' }]}>
            <InputNumber min={0} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item
            name="suppress_duration"
            label="重复告警间隔(秒)"
            extra="告警持续未恢复时，按这个间隔重复通知；0 表示不重复发送。"
            rules={[{ required: true, message: '请输入重复告警间隔' }]}
          >
            <InputNumber min={0} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item
            name="mention_users_text"
            label="@对象"
            extra="多个对象用英文逗号分隔，例如：hongyu.zhu, zhangsan"
          >
            <Input placeholder="例如：hongyu.zhu, zhangsan" />
          </Form.Item>
          <Form.Item
            name="extra_config_text"
            label="附加配置(JSON)"
            extra={'可填写 interface_name / interface_index / peer / keyword / lookback_seconds / severity_lte / circuit_id / circuit_name；线路流量掉底阈值按 Mbps 填写，例如 {"interface_name":"Ten-GigabitEthernet1/0/1"}'}
            rules={[
              {
                validator: async (_, value) => {
                  if (!value) return
                  JSON.parse(value)
                },
              },
            ]}
          >
            <Input.TextArea rows={5} placeholder='例如 {"keyword":"BFD Down","lookback_seconds":300}' />
          </Form.Item>
          <Form.Item
            name="webhook_url"
            label="Webhook 机器人地址"
            extra={`支持飞书 / 企业微信 / 钉钉自动识别，当前识别：${detectedWebhookLabel}`}
            rules={[
              {
                type: 'url',
                warningOnly: true,
                message: '请输入有效的 URL',
              },
            ]}
          >
            <Input
              placeholder="支持飞书 / 企业微信 / 钉钉 / 通用 Webhook"
              onChange={(event) => {
                setDetectedWebhookLabel(detectWebhookChannel(event.target.value).label)
              }}
            />
          </Form.Item>
          <Form.Item>
            <Button onClick={handleTestWebhook} loading={testingWebhook}>
              发送测试消息
            </Button>
          </Form.Item>
          <Form.Item name="severity" label="严重级别" rules={[{ required: true, message: '请选择级别' }]}>
            <Select
              options={[
                { value: 'P0', label: 'P0' },
                { value: 'P1', label: 'P1' },
                { value: 'P2', label: 'P2' },
                { value: 'P3', label: 'P3' },
              ]}
            />
          </Form.Item>
          <Form.Item name="enabled" label="启用规则" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Drawer>
    </Card>
  )
}

export default AlertRules
