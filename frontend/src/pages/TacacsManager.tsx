import { useEffect, useState } from 'react'
import { Button, Card, DatePicker, Divider, Form, Input, InputNumber, Popconfirm, Select, Space, Switch, Table, Tabs, Tag, Tooltip, Typography, message, theme } from 'antd'
import { DeleteOutlined, PauseCircleOutlined, PlayCircleOutlined, PlusOutlined, ReloadOutlined, SaveOutlined } from '@ant-design/icons'
import {
  getTacacsConfig,
  getTacacsLogs,
  getTacacsStatus,
  restartTacacs,
  saveTacacsConfig,
  saveTacacsNotifications,
  startTacacs,
  stopTacacs,
  testTacacsNotification,
  type TacacsCommandLog,
  type TacacsServiceStatus,
  type TacacsSettings,
} from '../api/tacacs'
import { useAuthStore } from '../store/auth'

const { Text } = Typography
const { RangePicker } = DatePicker

const compactCardStyle = { borderRadius: 6 } as const
const hiddenDefaults = {
  key: 'para@2026',
  accounting_file: '/var/log/tacacs+/tacacs.log',
}

const defaultSettings: TacacsSettings = {
  ...hiddenDefaults,
  roles: [
    {
      name: 'network_admin',
      priv_lvl: 15,
      default_permit: true,
      commands: [
        { name: 'display', permit: ['.*'], deny: [] },
        { name: 'system-view', permit: ['.*'], deny: [] },
      ],
    },
  ],
  users: [{ username: 'lvfz', password: '234', role: 'network_admin' }],
  notification_channels: [],
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

const toText = (items?: string[]) => (items || []).join('\n')
const toList = (text?: string) => (text || '').split('\n').map((item) => item.trim()).filter(Boolean)
const isMaskedSecret = (value?: string) => (value || '').trim() === '******'
const hasUrlProtocol = (value?: string) => /^https?:\/\//i.test((value || '').trim())

const validateWebhookUrl = (_: any, value?: string) => {
  const text = (value || '').trim()
  if (!text) return Promise.reject(new Error('请输入 webhook 链接'))
  if (isMaskedSecret(text)) return Promise.resolve()
  if (!hasUrlProtocol(text)) return Promise.reject(new Error('Webhook 地址必须以 http:// 或 https:// 开头'))
  return Promise.resolve()
}

const toFormValues = (settings?: TacacsSettings) => {
  const source = settings || defaultSettings
  return {
    ...hiddenDefaults,
    ...source,
    roles: (source.roles || []).map((role) => ({
      ...role,
      commands: (role.commands || []).map((command) => ({
        name: command.name,
        permitText: toText(command.permit),
        denyText: toText(command.deny),
      })),
    })),
    notification_channels: (source.notification_channels || []).map((channel) => ({
      ...channel,
      type: channel.type || detectWebhookChannel(channel.webhook).type,
    })),
  }
}

const toSettings = (values: any): TacacsSettings => ({
  key: values.key || hiddenDefaults.key,
  accounting_file: values.accounting_file || hiddenDefaults.accounting_file,
  roles: (values.roles || []).map((role: any) => ({
    name: role.name,
    priv_lvl: role.priv_lvl,
    default_permit: Boolean(role.default_permit),
    commands: (role.commands || []).map((command: any) => ({
      name: command.name,
      permit: toList(command.permitText),
      deny: toList(command.denyText),
    })),
  })),
  users: values.users || [],
  notification_channels: (values.notification_channels || [])
    .filter((channel: any) => channel?.webhook)
    .map((channel: any) => ({
      webhook: channel.webhook,
      type: detectWebhookChannel(channel.webhook).type,
    })),
})

const sectionBoxStyle = {
  borderRadius: 6,
  padding: 12,
} as const

const rowGrid = (columns: string) => ({
  display: 'grid',
  gridTemplateColumns: columns,
  gap: 8,
  alignItems: 'start',
}) as const

type TacacsManagerProps = {
  activeTab?: 'config' | 'logs'
}

const TacacsManager = ({ activeTab = 'config' }: TacacsManagerProps) => {
  const [configForm] = Form.useForm()
  const [loadingConfig, setLoadingConfig] = useState(false)
  const [saving, setSaving] = useState(false)
  const [logs, setLogs] = useState<TacacsCommandLog[]>([])
  const [logLoading, setLogLoading] = useState(false)
  const [serviceStatus, setServiceStatus] = useState<TacacsServiceStatus>()
  const [statusLoading, setStatusLoading] = useState(false)
  const [starting, setStarting] = useState(false)
  const [restarting, setRestarting] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [testingWebhook, setTestingWebhook] = useState<Record<number, boolean>>({})
  const [savingWebhook, setSavingWebhook] = useState<Record<number, boolean>>({})
  const [activeRoleKey, setActiveRoleKey] = useState('0')
  const [search, setSearch] = useState('')
  const [logDevice, setLogDevice] = useState('')
  const [logUser, setLogUser] = useState('')
  const [logCommand, setLogCommand] = useState('')
  const [logTimeRange, setLogTimeRange] = useState<any>(null)
  const [logPage, setLogPage] = useState(1)
  const [logPageSize, setLogPageSize] = useState(20)
  const [logTotal, setLogTotal] = useState(0)
  const [logPath, setLogPath] = useState('')
  const canModify = !useAuthStore((state) => state.user?.read_only)
  const {
    token: { colorBgContainer, colorFillAlter, colorBorder },
  } = theme.useToken()
  const sectionStyle = {
    ...sectionBoxStyle,
    border: `1px solid ${colorBorder}`,
    background: colorBgContainer,
  } as const

  const roleOptions = (Form.useWatch('roles', configForm) || []).map((role: any) => ({
    value: role?.name,
    label: role?.name,
  })).filter((item: any) => item.value)
  const notificationChannels = Form.useWatch('notification_channels', configForm) || []

  const fetchConfig = async () => {
    setLoadingConfig(true)
    try {
      const result = await getTacacsConfig()
      configForm.setFieldsValue(toFormValues(result.settings))
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '读取 Tacacs 配置失败')
    } finally {
      setLoadingConfig(false)
    }
  }

  const fetchServiceStatus = async () => {
    setStatusLoading(true)
    try {
      setServiceStatus(await getTacacsStatus())
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '读取 Tacacs 服务状态失败')
    } finally {
      setStatusLoading(false)
    }
  }

  const fetchLogs = async (page = logPage, pageSize = logPageSize) => {
    setLogLoading(true)
    try {
      const result = await getTacacsLogs({
        skip: (page - 1) * pageSize,
        limit: pageSize,
        search: search || undefined,
        device_ip: logDevice || undefined,
        username: logUser || undefined,
        command: logCommand || undefined,
        start_time: logTimeRange?.[0]?.format?.('YYYY-MM-DD HH:mm:ss'),
        end_time: logTimeRange?.[1]?.format?.('YYYY-MM-DD HH:mm:ss'),
      })
      setLogs(result.items)
      setLogTotal(result.total)
      setLogPage(page)
      setLogPageSize(pageSize)
      setLogPath(result.path)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '读取 Tacacs 日志失败')
    } finally {
      setLogLoading(false)
    }
  }

  useEffect(() => {
    fetchConfig()
    fetchServiceStatus()
    fetchLogs()
  }, [])

  useEffect(() => {
    if (activeTab !== 'logs') return
    const timer = window.setTimeout(() => {
      fetchLogs(1, logPageSize)
    }, 450)
    return () => window.clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab, search, logDevice, logUser, logCommand, logTimeRange])

  const handleSave = async () => {
    const values = await configForm.validateFields()
    setSaving(true)
    try {
      await saveTacacsConfig(toSettings(values))
      message.success('Tacacs 配置已保存，重启 Tacacs 服务后生效')
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '保存 Tacacs 配置失败')
    } finally {
      setSaving(false)
    }
  }

  const handleRestart = async () => {
    setRestarting(true)
    try {
      const result = await restartTacacs()
      setServiceStatus(result)
      message.success(result.message || 'Tacacs 服务已重启')
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '重启 Tacacs 服务失败')
    } finally {
      setRestarting(false)
    }
  }

  const handleStart = async () => {
    setStarting(true)
    try {
      const result = await startTacacs()
      setServiceStatus(result)
      message.success(result.message || 'Tacacs 服务已启动')
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '启动 Tacacs 服务失败')
    } finally {
      setStarting(false)
    }
  }

  const handleStop = async () => {
    setStopping(true)
    try {
      const result = await stopTacacs()
      setServiceStatus(result)
      message.success(result.message || 'Tacacs 服务已停止')
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '停止 Tacacs 服务失败')
    } finally {
      setStopping(false)
    }
  }

  const handleTestWebhook = async (index: number) => {
    const webhook = (configForm.getFieldValue(['notification_channels', index, 'webhook']) || '').trim()
    if (!webhook) {
      message.warning('请先填写机器人地址')
      return
    }
    if (!isMaskedSecret(webhook) && !hasUrlProtocol(webhook)) {
      message.warning('Webhook 地址必须以 http:// 或 https:// 开头')
      return
    }
    setTestingWebhook((prev) => ({ ...prev, [index]: true }))
    try {
      const result = await testTacacsNotification(webhook, index)
      message.success(result.message)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '测试发送失败')
    } finally {
      setTestingWebhook((prev) => ({ ...prev, [index]: false }))
    }
  }

  const handleSaveWebhook = async (index: number) => {
    try {
      await configForm.validateFields([['notification_channels', index, 'webhook']])
      const channels = (configForm.getFieldValue('notification_channels') || [])
        .filter((channel: any) => channel?.webhook)
        .map((channel: any) => ({
          webhook: channel.webhook,
          type: detectWebhookChannel(channel.webhook).type,
        }))
      setSavingWebhook((prev) => ({ ...prev, [index]: true }))
      const result = await saveTacacsNotifications(channels)
      configForm.setFieldValue('notification_channels', result.settings.notification_channels || [])
      message.success(result.message || '机器人通知已保存')
    } catch (error: any) {
      if (error?.errorFields) {
        return
      }
      message.error(error?.response?.data?.detail || '保存机器人通知失败')
    } finally {
      setSavingWebhook((prev) => ({ ...prev, [index]: false }))
    }
  }

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <div style={{ color: '#8c8c8c', fontSize: 13 }}>系统管理 / Tacacs 管理</div>
      <Tabs
        activeKey={activeTab}
        tabBarStyle={{ display: 'none' }}
        items={[
          {
            key: 'config',
            label: '配置管理',
            children: (
              <Card
                style={compactCardStyle}
                bodyStyle={{ padding: 16 }}
                title="Tacacs 配置管理"
                extra={
                  <Space>
                    <Tag color={serviceStatus?.running ? 'success' : serviceStatus?.status === 'stopped' ? 'default' : 'warning'}>
                      当前状态：{statusLoading ? '读取中' : serviceStatus?.label || '未知'}
                    </Tag>
                    <Tooltip title="刷新配置和服务状态">
                      <Button icon={<ReloadOutlined />} loading={loadingConfig || statusLoading} onClick={() => { void fetchConfig(); void fetchServiceStatus() }}>刷新</Button>
                    </Tooltip>
                    {canModify ? (
                      <>
                        {!serviceStatus?.running ? (
                          <Button type="primary" icon={<PlayCircleOutlined />} loading={starting} onClick={handleStart}>启动 Tacacs 服务</Button>
                        ) : (
                          <>
                            <Tooltip title="重启 Tacacs 服务">
                              <Button icon={<ReloadOutlined />} loading={restarting} onClick={handleRestart}>重启 Tacacs 服务</Button>
                            </Tooltip>
                            <Popconfirm
                              title="确认停止 Tacacs 服务？"
                              description="停止后，网络设备通过 Tacacs 登录和命令授权可能不可用。"
                              okText="确认停止"
                              cancelText="取消"
                              onConfirm={handleStop}
                            >
                              <Button danger icon={<PauseCircleOutlined />} loading={stopping}>停止 Tacacs 服务</Button>
                            </Popconfirm>
                          </>
                        )}
                        <Tooltip title="保存配置">
                          <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={handleSave}>保存配置</Button>
                        </Tooltip>
                      </>
                    ) : null}
                  </Space>
                }
              >
                <Form form={configForm} layout="vertical" size="small" disabled={!canModify} initialValues={toFormValues(defaultSettings)}>
                  <Form.Item name="key" hidden><Input /></Form.Item>
                  <Form.Item name="accounting_file" hidden><Input /></Form.Item>

                  <div style={{ display: 'grid', gridTemplateColumns: 'minmax(360px, 1fr) minmax(420px, 1.25fr)', gap: 12, alignItems: 'start' }}>
                    <div style={sectionStyle}>
                      <Divider orientation="left" style={{ margin: '0 0 10px' }}>账号</Divider>
                      <Form.List name="users">
                        {(fields, { add, remove }) => (
                          <Space direction="vertical" style={{ width: '100%' }} size={6}>
                            {fields.map((field) => (
                              <div key={field.key} style={rowGrid('1fr 1fr 1fr 32px')}>
                                <Form.Item {...field} name={[field.name, 'username']} label="账号" rules={[{ required: true, message: '请输入账号' }]}>
                                  <Input placeholder="lvfz" />
                                </Form.Item>
                                <Form.Item {...field} name={[field.name, 'password']} label="密码" rules={[{ required: true, message: '请输入密码' }]}>
                                  <Input.Password visibilityToggle={false} placeholder="234" />
                                </Form.Item>
                                <Form.Item {...field} name={[field.name, 'role']} label="所属组" rules={[{ required: true, message: '请选择组' }]}>
                                  <Select placeholder="network_admin" options={roleOptions} />
                                </Form.Item>
                                {canModify ? (
                                  <Tooltip title="删除账号">
                                    <Button danger icon={<DeleteOutlined />} onClick={() => remove(field.name)} style={{ marginTop: 24 }} />
                                  </Tooltip>
                                ) : null}
                              </div>
                            ))}
                            {canModify ? (
                              <Tooltip title="添加账号">
                                <Button size="small" icon={<PlusOutlined />} onClick={() => add({ username: '', password: '', role: roleOptions[0]?.value })}>添加账号</Button>
                              </Tooltip>
                            ) : null}
                          </Space>
                        )}
                      </Form.List>
                    </div>

                    <div style={sectionStyle}>
                      <Divider orientation="left" style={{ margin: '0 0 10px' }}>组与命令权限</Divider>
                      <Form.List name="roles">
                        {(roleFields, { add: addRole, remove: removeRole }) => (
                          <Space direction="vertical" style={{ width: '100%' }} size={8}>
                            <Tabs
                              type="card"
                              size="small"
                              activeKey={roleFields.some((field) => String(field.key) === activeRoleKey) ? activeRoleKey : String(roleFields[0]?.key ?? '0')}
                              onChange={setActiveRoleKey}
                              items={roleFields.map((roleField) => {
                                const roleName = configForm.getFieldValue(['roles', roleField.name, 'name']) || `组 ${roleField.name + 1}`
                                return {
                                  key: String(roleField.key),
                                  label: roleName,
                                  children: (
                                    <div style={{ border: `1px solid ${colorBorder}`, borderRadius: 6, padding: 8, background: colorFillAlter }}>
                                      <div style={rowGrid('1fr 96px 120px 32px')}>
                                        <Form.Item {...roleField} name={[roleField.name, 'name']} label="组名称" rules={[{ required: true, message: '请输入组名称' }]}>
                                          <Input placeholder="network_admin" />
                                        </Form.Item>
                                        <Form.Item {...roleField} name={[roleField.name, 'priv_lvl']} label="级别" rules={[{ required: true, message: '请输入权限级别' }]}>
                                          <InputNumber min={0} max={15} style={{ width: '100%' }} />
                                        </Form.Item>
                                        <Form.Item {...roleField} name={[roleField.name, 'default_permit']} label="默认放行" valuePropName="checked" extra="开启后未命中命令规则的命令也会放行">
                                          <Switch checkedChildren="是" unCheckedChildren="否" />
                                        </Form.Item>
                                        {canModify ? (
                                          <Tooltip title="删除组">
                                            <Button danger icon={<DeleteOutlined />} onClick={() => removeRole(roleField.name)} style={{ marginTop: 24 }} />
                                          </Tooltip>
                                        ) : null}
                                      </div>
                                      <Form.List name={[roleField.name, 'commands']}>
                                        {(commandFields, { add: addCommand, remove: removeCommand }) => (
                                          <Space direction="vertical" style={{ width: '100%' }} size={6}>
                                            {commandFields.map((commandField) => (
                                              <div key={commandField.key} style={rowGrid('120px 1fr 1fr 32px')}>
                                                <Form.Item {...commandField} name={[commandField.name, 'name']} label="命令" rules={[{ required: true, message: '请输入命令' }]}>
                                                  <Input placeholder="display" />
                                                </Form.Item>
                                                <Form.Item {...commandField} name={[commandField.name, 'permitText']} label="允许规则">
                                                  <Input.TextArea rows={2} placeholder={'.*'} />
                                                </Form.Item>
                                                <Form.Item {...commandField} name={[commandField.name, 'denyText']} label="拒绝规则">
                                                  <Input.TextArea rows={2} placeholder="shutdown.*" />
                                                </Form.Item>
                                                {canModify ? (
                                                  <Tooltip title="删除命令权限">
                                                    <Button danger icon={<DeleteOutlined />} onClick={() => removeCommand(commandField.name)} style={{ marginTop: 24 }} />
                                                  </Tooltip>
                                                ) : null}
                                              </div>
                                            ))}
                                            {canModify ? (
                                              <Tooltip title="添加命令权限">
                                                <Button size="small" icon={<PlusOutlined />} onClick={() => addCommand({ name: '', permitText: '.*', denyText: '' })}>添加命令权限</Button>
                                              </Tooltip>
                                            ) : null}
                                          </Space>
                                        )}
                                      </Form.List>
                                    </div>
                                  ),
                                }
                              })}
                            />
                            {canModify ? (
                              <Tooltip title="添加组">
                                <Button size="small" icon={<PlusOutlined />} onClick={() => {
                                  addRole({ name: '', priv_lvl: 1, default_permit: false, commands: [] })
                                }}>添加组</Button>
                              </Tooltip>
                            ) : null}
                          </Space>
                        )}
                      </Form.List>
                    </div>
                  </div>

                  <div style={{ ...sectionStyle, marginTop: 12 }}>
                    <Divider orientation="left" style={{ margin: '0 0 10px' }}>机器人通知</Divider>
                    <Form.List name="notification_channels">
                      {(fields, { add, remove }) => (
                        <Space direction="vertical" style={{ width: '100%' }} size={6}>
                          {fields.map((field) => {
                            const webhook = notificationChannels?.[field.name]?.webhook
                            const detected = detectWebhookChannel(webhook)
                            return (
                              <div key={field.key} style={rowGrid('1fr 140px 72px 72px 32px')}>
                                <Form.Item {...field} name={[field.name, 'type']} hidden><Input /></Form.Item>
                                <Form.Item
                                  {...field}
                                  name={[field.name, 'webhook']}
                                  label="Webhook 地址"
                                  extra={`当前识别：${detected.label}`}
                                  rules={[{ validator: validateWebhookUrl }]}
                                >
                                  <Input.Password
                                    visibilityToggle={false}
                                    placeholder="支持飞书 / 企业微信 / 钉钉自动识别"
                                    onChange={(event) => {
                                      configForm.setFieldValue(['notification_channels', field.name, 'type'], detectWebhookChannel(event.target.value).type)
                                    }}
                                  />
                                </Form.Item>
                                <div style={{ marginTop: 27 }}>
                                  <Text type="secondary">{detected.label}</Text>
                                </div>
                                {canModify ? (
                                  <>
                                    <Tooltip title="测试机器人">
                                      <Button style={{ marginTop: 24 }} loading={testingWebhook[field.name]} onClick={() => handleTestWebhook(field.name)}>测试</Button>
                                    </Tooltip>
                                    <Tooltip title="保存机器人">
                                      <Button type="primary" style={{ marginTop: 24 }} loading={savingWebhook[field.name]} onClick={() => handleSaveWebhook(field.name)}>确定</Button>
                                    </Tooltip>
                                    <Tooltip title="删除机器人">
                                      <Button danger icon={<DeleteOutlined />} onClick={() => remove(field.name)} style={{ marginTop: 24 }} />
                                    </Tooltip>
                                  </>
                                ) : null}
                              </div>
                            )
                          })}
                          {canModify ? (
                            <Tooltip title="添加机器人">
                              <Button size="small" icon={<PlusOutlined />} onClick={() => add({ type: 'webhook', webhook: '' })}>添加机器人</Button>
                            </Tooltip>
                          ) : null}
                        </Space>
                      )}
                    </Form.List>
                  </div>
                </Form>
              </Card>
            ),
          },
          {
            key: 'logs',
            label: '操作日志',
            children: (
              <Card
                style={compactCardStyle}
                title="Tacacs 命令操作日志"
              >
                <Space direction="vertical" size={8} style={{ width: '100%', marginBottom: 12 }}>
                  <Space wrap>
                    <RangePicker
                      showTime
                      value={logTimeRange}
                      onChange={setLogTimeRange}
                      style={{ width: 360 }}
                      placeholder={['开始时间', '结束时间']}
                    />
                    <Input allowClear placeholder="设备地址" value={logDevice} onChange={(event) => setLogDevice(event.target.value)} onPressEnter={() => fetchLogs(1, logPageSize)} style={{ width: 180 }} />
                    <Input allowClear placeholder="账号" value={logUser} onChange={(event) => setLogUser(event.target.value)} onPressEnter={() => fetchLogs(1, logPageSize)} style={{ width: 140 }} />
                    <Input allowClear placeholder="命令" value={logCommand} onChange={(event) => setLogCommand(event.target.value)} onPressEnter={() => fetchLogs(1, logPageSize)} style={{ width: 220 }} />
                    <Input allowClear placeholder="全文搜索" value={search} onChange={(event) => setSearch(event.target.value)} onPressEnter={() => fetchLogs(1, logPageSize)} style={{ width: 220 }} />
                    <Tooltip title="按当前条件刷新日志">
                      <Button icon={<ReloadOutlined />} loading={logLoading} onClick={() => fetchLogs(1, logPageSize)}>刷新</Button>
                    </Tooltip>
                  </Space>
                  <div style={{ color: '#8c8c8c' }}>{logPath || '日志路径加载中'}，共 {logTotal} 条匹配记录</div>
                </Space>
                <Table<TacacsCommandLog>
                  rowKey={(record) => `${record.time}-${record.device_ip}-${record.username}-${record.command}-${record.raw}`}
                  size="small"
                  loading={logLoading}
                  dataSource={logs}
                  scroll={{ x: 1100, y: 520 }}
                  pagination={{
                    current: logPage,
                    pageSize: logPageSize,
                    total: logTotal,
                    showSizeChanger: true,
                    pageSizeOptions: ['10', '20', '50', '100', '200'],
                    showTotal: (total) => `共 ${total} 条`,
                  }}
                  onChange={(pagination) => fetchLogs(pagination.current || 1, pagination.pageSize || logPageSize)}
                  columns={[
                    {
                      title: '时间',
                      dataIndex: 'time',
                      width: 180,
                      fixed: 'left',
                    },
                    {
                      title: '设备IP',
                      dataIndex: 'device_ip',
                      width: 150,
                    },
                    {
                      title: '用户',
                      dataIndex: 'username',
                      width: 120,
                    },
                    {
                      title: '命令',
                      dataIndex: 'command',
                      width: 650,
                      render: (value: string) => (
                        <span style={{ fontFamily: 'Menlo, Consolas, monospace', whiteSpace: 'nowrap' }}>
                          {value}
                        </span>
                      ),
                    },
                  ]}
                />
              </Card>
            ),
          },
        ]}
      />
    </Space>
  )
}

export default TacacsManager
