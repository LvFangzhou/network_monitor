import { useEffect, useState } from 'react'
import { Button, Card, DatePicker, Form, Input, Modal, Popconfirm, Select, Space, Switch, Table, Tag, Tooltip, Typography, message } from 'antd'
import { DeleteOutlined, EditOutlined, EyeOutlined, PlusOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { useNavigate } from 'react-router-dom'
import {
  createAlertSilence,
  deleteAlertSilence,
  getAlertSilenceMatches,
  getAlertSilences,
  updateAlertSilence,
  type AlertHistory as AlertHistoryItem,
  type AlertSilence,
  type AlertSilencePayload,
} from '../../api/alerts'
import { useAuthStore } from '../../store/auth'

const tablePagination = {
  defaultPageSize: 20,
  showSizeChanger: true,
  pageSizeOptions: [10, 20, 50, 100],
  showTotal: (total: number, range: [number, number]) => `第 ${range[0]}-${range[1]} 条 / 共 ${total} 条`,
}

const getEffectiveStatus = (record: AlertSilence) => {
  if (!record.enabled) {
    return { color: 'default', text: '已停用' }
  }
  const now = dayjs()
  if (record.starts_at && dayjs(record.starts_at).isAfter(now)) {
    return { color: 'gold', text: '未开始' }
  }
  if (record.expires_at && dayjs(record.expires_at).isBefore(now)) {
    return { color: 'red', text: '已过期' }
  }
  return { color: 'green', text: '生效中' }
}

const statusColors: Record<string, string> = {
  firing: 'red',
  acknowledged: 'gold',
  ignored: 'default',
  snoozed: 'blue',
  resolved: 'green',
}

const silenceExamples = [
  {
    title: '屏蔽单个端口',
    rows: [
      ['IP', '包含其中任一', '10.254.1.92'],
      ['接口', '包含其中任一', '0/116'],
    ],
  },
  {
    title: '屏蔽很多接口',
    rows: [
      ['IP', '包含其中任一', '10.242.2.17\n10.242.2.18\n10.242.2.19'],
      ['接口', '正则匹配', '(^|[^0-9])0/([0-9]|[1-3][0-9]|4[0-7])([^0-9]|$)'],
    ],
    note: '这个例子表示屏蔽 0/0 到 0/47 接口。',
  },
  {
    title: '屏蔽整机',
    rows: [
      ['IP', '包含其中任一', '10.254.1.65'],
    ],
    note: '只填 IP，不填接口，就会匹配这台设备的所有告警。',
  },
  {
    title: '屏蔽 BGP 告警',
    rows: [
      ['IP', '包含其中任一', '10.242.2.11\n10.242.2.12'],
      ['消息内容', '包含其中任一', 'BGP'],
    ],
  },
]

const statusLabels: Record<string, string> = {
  firing: '触发中',
  acknowledged: '已确认',
  ignored: '已忽略',
  snoozed: '暂停复查',
  resolved: '已解决',
}

const AlertSilences = () => {
  const [items, setItems] = useState<AlertSilence[]>([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editingItem, setEditingItem] = useState<AlertSilence | null>(null)
  const [matchesOpen, setMatchesOpen] = useState(false)
  const [matchesLoading, setMatchesLoading] = useState(false)
  const [matchesTitle, setMatchesTitle] = useState('')
  const [matches, setMatches] = useState<AlertHistoryItem[]>([])
  const [matchesTotal, setMatchesTotal] = useState(0)
  const [matchesPage, setMatchesPage] = useState(1)
  const [matchesPageSize, setMatchesPageSize] = useState(10)
  const [selectedSilence, setSelectedSilence] = useState<AlertSilence | null>(null)
  const [form] = Form.useForm()
  const currentUser = useAuthStore((state) => state.user)
  const navigate = useNavigate()
  const canModify = !currentUser?.read_only

  const fetchData = async (options?: { silent?: boolean }) => {
    if (!options?.silent) {
      setLoading(true)
    }
    try {
      const result = await getAlertSilences()
      setItems(result.items)
    } catch {
      message.error('获取告警屏蔽失败')
    } finally {
      if (!options?.silent) {
        setLoading(false)
      }
    }
  }

  useEffect(() => {
    fetchData()
  }, [])

  const openCreate = () => {
    setEditingItem(null)
    form.resetFields()
    form.setFieldsValue({
      enabled: true,
      starts_at: dayjs(),
      expires_at: null,
      conditions: [{ field: 'ip', operator: 'contains', value: '' }],
    })
    setModalOpen(true)
  }

  const openEdit = (item: AlertSilence) => {
    setEditingItem(item)
    form.setFieldsValue({
      ...item,
      starts_at: item.starts_at ? dayjs(item.starts_at) : null,
      expires_at: item.expires_at ? dayjs(item.expires_at) : null,
      conditions: item.conditions?.length ? item.conditions : [{ field: 'ip', operator: 'contains', value: '' }],
    })
    setModalOpen(true)
  }

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields()
      const payload: AlertSilencePayload = {
        name: values.name,
        rule_id: null,
        device_id: null,
        target_pattern: null,
        include_device_ip: null,
        include_interface: null,
        include_message: null,
        exclude_device_ip: null,
        exclude_interface: null,
        exclude_message: null,
        starts_at: values.starts_at ? values.starts_at.toISOString() : null,
        conditions: (values.conditions || []).filter((item: any) => item?.field && item?.operator && item?.value),
        reason: values.reason || null,
        enabled: values.enabled,
        expires_at: values.expires_at ? values.expires_at.toISOString() : null,
        actor_username: currentUser?.username || null,
      }
      let savedItem: AlertSilence
      if (editingItem) {
        savedItem = await updateAlertSilence(editingItem.id, payload)
        setItems((prev) => prev.map((item) => (
          item.id === savedItem.id
            ? {
                ...savedItem,
                matched_active_alerts: item.matched_active_alerts,
                matched_total_alerts: item.matched_total_alerts,
              }
            : item
        )))
        message.success('告警屏蔽已更新')
      } else {
        savedItem = await createAlertSilence(payload)
        setItems((prev) => [
          { ...savedItem, matched_active_alerts: 0, matched_total_alerts: 0 },
          ...prev,
        ])
        message.success('告警屏蔽已创建')
      }
      setModalOpen(false)
      window.setTimeout(() => {
        void fetchData({ silent: true })
      }, 300)
    } catch (error: any) {
      if (!error?.errorFields) {
        message.error(error?.response?.data?.detail || '保存失败')
      }
    }
  }

  const fetchMatches = async (silence: AlertSilence, nextPage = 1, nextPageSize = matchesPageSize) => {
    setMatchesLoading(true)
    try {
      const result = await getAlertSilenceMatches(silence.id, {
        skip: (nextPage - 1) * nextPageSize,
        limit: nextPageSize,
        active_only: false,
      })
      setMatches(result.items)
      setMatchesTotal(result.total)
      setMatchesPage(nextPage)
      setMatchesPageSize(nextPageSize)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '获取命中告警失败')
    } finally {
      setMatchesLoading(false)
    }
  }

  const openMatches = async (record: AlertSilence) => {
    setSelectedSilence(record)
    setMatchesTitle(record.name)
    setMatchesOpen(true)
    await fetchMatches(record, 1, matchesPageSize)
  }

  return (
    <Card
      title="告警屏蔽"
      extra={
        canModify ? (
          <Tooltip title="新建屏蔽">
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>
              新建屏蔽
            </Button>
          </Tooltip>
        ) : null
      }
    >
      <Table
        rowKey="id"
        loading={loading}
        dataSource={items}
        pagination={tablePagination}
        columns={[
          { title: '名称', dataIndex: 'name' },
          {
            title: '匹配条件',
            render: (_: unknown, record: AlertSilence) => (
              <Space direction="vertical" size={0}>
                {(record.conditions || []).map((condition, index) => (
                  <span key={`${condition.field}-${index}`}>{condition.field} / {condition.operator} / {condition.value}</span>
                ))}
                {!record.conditions?.length ? <span>-</span> : null}
              </Space>
            ),
          },
          { title: '开始时间', dataIndex: 'starts_at', render: (value?: string | null) => value ? new Date(value).toLocaleString() : '立即生效' },
          { title: '结束时间', dataIndex: 'expires_at', render: (value?: string | null) => value ? new Date(value).toLocaleString() : '永久' },
          { title: '备注', dataIndex: 'reason', render: (value?: string | null) => value || '-' },
          {
            title: '启用状态',
            dataIndex: 'enabled',
            render: (value: boolean) => (
              <Tag color={value ? 'blue' : 'default'}>{value ? '启用' : '停用'}</Tag>
            ),
          },
          {
            title: '有效状态',
            render: (_: unknown, record: AlertSilence) => {
              const status = getEffectiveStatus(record)
              return <Tag color={status.color}>{status.text}</Tag>
            },
          },
          {
            title: '命中告警',
            render: (_: unknown, record: AlertSilence) => {
              const activeCount = record.matched_active_alerts || 0
              const totalCount = record.matched_total_alerts || 0
              return (
                <Space size={6}>
                  <Tooltip title="当前仍处于触发、确认、忽略或暂缓状态的命中告警">
                    <Tag color={activeCount > 0 ? 'red' : 'default'}>当前 {activeCount} 条</Tag>
                  </Tooltip>
                  <Tooltip title="历史上匹配过这条屏蔽规则的告警">
                    <Tag color={totalCount > 0 ? 'blue' : 'default'}>历史 {totalCount} 条</Tag>
                  </Tooltip>
                  <Tooltip title="查看历史命中告警">
                    <Button
                      type="text"
                      size="small"
                      icon={<EyeOutlined />}
                      disabled={totalCount === 0}
                      onClick={() => openMatches(record)}
                    />
                  </Tooltip>
                </Space>
              )
            },
          },
          {
            title: '操作',
            render: (_: unknown, record: AlertSilence) => (
              <Space>
                {canModify ? (
                  <>
                    <Tooltip title="编辑">
                      <Button type="text" icon={<EditOutlined />} onClick={() => openEdit(record)} />
                    </Tooltip>
                    <Popconfirm title="确认删除这条屏蔽吗？" onConfirm={() => deleteAlertSilence(record.id).then(() => fetchData())}>
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
        title={editingItem ? '编辑告警屏蔽' : '新建告警屏蔽'}
        width={1040}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={handleSubmit}
        okText="提交"
        cancelText="重置"
        centered
        destroyOnClose
        afterClose={() => {
          setEditingItem(null)
          form.resetFields()
        }}
        footer={[
          <Button key="reset" onClick={() => form.resetFields()}>
            重置
          </Button>,
          <Button key="submit" type="primary" onClick={handleSubmit}>
            提交
          </Button>,
        ]}
      >
        <Form form={form} layout="vertical">
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 300px', gap: 16, alignItems: 'start' }}>
            <div>
              <Form.Item name="name" label="屏蔽名称" rules={[{ required: true, message: '请输入屏蔽名称' }]}>
                <Input />
              </Form.Item>
              <Form.Item name="starts_at" label="开始时间" rules={[{ required: true, message: '请选择开始时间' }]}>
                <DatePicker
                  showTime
                  allowClear
                  style={{ width: '100%' }}
                  placeholder="选择屏蔽开始时间"
                  format="YYYY-MM-DD HH:mm:ss"
                />
              </Form.Item>
              <Form.Item name="expires_at" label="失效时间（留空表示永久）">
                <DatePicker
                  showTime
                  needConfirm
                  allowClear
                  style={{ width: '100%' }}
                  placeholder="留空表示永久有效"
                  format="YYYY-MM-DD HH:mm:ss"
                />
              </Form.Item>
              <Form.Item name="reason" label="备注" rules={[{ required: true, message: '请输入备注' }]}>
                <Input.TextArea rows={4} />
              </Form.Item>
              <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 12 }}>
                过滤条件（条件之间是 AND 的关系）
              </Typography.Text>
              <Form.List name="conditions">
                {(fields, { add, remove }) => (
                  <Space direction="vertical" style={{ width: '100%' }} size={16}>
                    {fields.map((field, index) => (
                      <Card
                        key={field.key}
                        size="small"
                        title={`过滤条件 #${index + 1}`}
                        extra={fields.length > 1 ? (
                          <Tooltip title="删除过滤条件">
                            <Button type="text" danger icon={<DeleteOutlined />} onClick={() => remove(field.name)} />
                          </Tooltip>
                        ) : null}
                      >
                        {index === 0 ? (
                          <div style={{ display: 'grid', gridTemplateColumns: '120px 180px 1fr', gap: 12, marginBottom: 8, color: '#8c8c8c', fontSize: 12 }}>
                            <span>字段</span>
                            <span>比较符</span>
                            <span>值</span>
                          </div>
                        ) : null}
                        <div style={{ display: 'grid', gridTemplateColumns: '120px 180px 1fr', gap: 12, alignItems: 'start' }}>
                          <Form.Item
                            {...field}
                            name={[field.name, 'field']}
                            rules={[{ required: true, message: '请选择字段' }]}
                            style={{ marginBottom: 0 }}
                          >
                            <Select
                              options={[
                                { value: 'ip', label: 'IP' },
                                { value: 'interface', label: '接口' },
                                { value: 'message', label: '消息内容' },
                                { value: 'alarm_id', label: 'Alarm ID' },
                              ]}
                            />
                          </Form.Item>
                          <Form.Item
                            {...field}
                            name={[field.name, 'operator']}
                            rules={[{ required: true, message: '请选择比较符' }]}
                            style={{ marginBottom: 0 }}
                          >
                            <Select
                              options={[
                                { value: 'contains', label: '包含其中任一' },
                                { value: 'not_contains', label: '不包含其中任一' },
                                { value: 'regex', label: '正则匹配' },
                                { value: 'not_regex', label: '正则不匹配' },
                                { value: 'equals', label: '等于其中任一' },
                                { value: 'not_equals', label: '不等于全部' },
                              ]}
                            />
                          </Form.Item>
                          <Form.Item
                            {...field}
                            name={[field.name, 'value']}
                            rules={[{ required: true, message: '请输入匹配值' }]}
                            style={{ marginBottom: 0 }}
                          >
                            <Input.TextArea rows={3} placeholder="多个值可用逗号、分号或换行分隔" />
                          </Form.Item>
                        </div>
                      </Card>
                    ))}
                    <Tooltip title="新增过滤条件">
                      <Button icon={<PlusOutlined />} onClick={() => add({ field: 'ip', operator: 'contains', value: '' })}>
                      新增过滤条件
                      </Button>
                    </Tooltip>
                  </Space>
                )}
              </Form.List>
              <Form.Item name="enabled" label="启用" valuePropName="checked">
                <Switch />
              </Form.Item>
            </div>
            <div style={{ display: 'grid', gap: 10 }}>
              <Typography.Text strong>填写示例</Typography.Text>
              {silenceExamples.map((example) => (
                <Card key={example.title} size="small" title={example.title} bodyStyle={{ padding: 10 }}>
                  <div style={{ display: 'grid', gap: 6 }}>
                    {example.rows.map(([field, operator, value]) => (
                      <div key={`${example.title}-${field}`} style={{ fontSize: 12, lineHeight: 1.6 }}>
                        <Tag color="blue" style={{ marginInlineEnd: 4 }}>{field}</Tag>
                        <Tag style={{ marginInlineEnd: 4 }}>{operator}</Tag>
                        <Typography.Text code style={{ whiteSpace: 'pre-wrap' }}>{value}</Typography.Text>
                      </div>
                    ))}
                  </div>
                  {example.note ? (
                    <Typography.Text type="secondary" style={{ display: 'block', marginTop: 8, fontSize: 12 }}>
                      {example.note}
                    </Typography.Text>
                  ) : null}
                </Card>
              ))}
            </div>
          </div>
        </Form>
      </Modal>

      <Modal
        title={`${matchesTitle || '告警屏蔽'} - 命中告警明细`}
        width={1080}
        open={matchesOpen}
        footer={null}
        onCancel={() => {
          setMatchesOpen(false)
          setSelectedSilence(null)
          setMatches([])
          setMatchesTotal(0)
        }}
        destroyOnClose
      >
        <Table
          rowKey="id"
          loading={matchesLoading}
          dataSource={matches}
          size="small"
          pagination={{
            current: matchesPage,
            pageSize: matchesPageSize,
            total: matchesTotal,
            showSizeChanger: true,
            pageSizeOptions: [10, 20, 50, 100],
            showTotal: (total, range) => `第 ${range[0]}-${range[1]} 条 / 共 ${total} 条`,
            onChange: (nextPage, nextPageSize) => {
              if (selectedSilence) {
                fetchMatches(selectedSilence, nextPage, nextPageSize)
              }
            },
          }}
          columns={[
            {
              title: 'Alarm ID',
              dataIndex: 'alarm_id',
              width: 150,
              render: (value: string | null | undefined, record: AlertHistoryItem) => (
                <a onClick={() => navigate(`/alerts/history?alert_id=${record.id}`)}>
                  {value || record.id}
                </a>
              ),
            },
            {
              title: '设备',
              width: 220,
              render: (_: unknown, record: AlertHistoryItem) => (
                <Space direction="vertical" size={0}>
                  <span>{record.device_name || '-'}</span>
                  <Typography.Text type="secondary">{record.device_ip || '-'}</Typography.Text>
                </Space>
              ),
            },
            {
              title: '对象',
              width: 180,
              render: (_: unknown, record: AlertHistoryItem) => record.alert_target_name || record.alert_target_key || '-',
            },
            {
              title: '消息',
              dataIndex: 'message',
              ellipsis: true,
              render: (value?: string | null) => value || '-',
            },
            {
              title: '状态',
              dataIndex: 'status',
              width: 90,
              render: (value: string) => <Tag color={statusColors[value] || 'default'}>{statusLabels[value] || value}</Tag>,
            },
            {
              title: '开始时间',
              dataIndex: 'started_at',
              width: 170,
              render: (value?: string | null) => value ? new Date(value).toLocaleString() : '-',
            },
          ]}
        />
      </Modal>
    </Card>
  )
}

export default AlertSilences
