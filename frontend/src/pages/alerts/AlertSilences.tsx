import { useEffect, useRef, useState } from 'react'
import { Button, Card, DatePicker, Form, Input, Modal, Popconfirm, Select, Space, Spin, Switch, Table, Tag, Tooltip, Typography, message } from 'antd'
import { DeleteOutlined, EditOutlined, EyeOutlined, PlusOutlined, SearchOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { useNavigate } from 'react-router-dom'
import {
  createAlertSilence,
  deleteAlertSilence,
  getAlertSilenceMatchCounts,
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

const compareText = (left?: string | number | null, right?: string | number | null) =>
  String(left ?? '').localeCompare(String(right ?? ''), 'zh-CN', { numeric: true, sensitivity: 'base' })

const alertColumnSearch = (
  getValue: (record: AlertHistoryItem) => string | number | null | undefined,
  placeholder: string
) => ({
  filterDropdown: ({ setSelectedKeys, selectedKeys, confirm, clearFilters }: any) => (
    <div style={{ padding: 8, width: 240 }} onKeyDown={(event) => event.stopPropagation()}>
      <Input
        autoFocus
        allowClear
        placeholder={placeholder}
        value={selectedKeys[0]}
        onChange={(event) => setSelectedKeys(event.target.value ? [event.target.value] : [])}
        onPressEnter={() => confirm()}
        style={{ marginBottom: 8, display: 'block' }}
      />
      <Space>
        <Button type="primary" size="small" icon={<SearchOutlined />} onClick={() => confirm()}>
          搜索
        </Button>
        <Button
          size="small"
          onClick={() => {
            clearFilters?.()
            confirm()
          }}
        >
          重置
        </Button>
      </Space>
    </div>
  ),
  filterIcon: (filtered: boolean) => <SearchOutlined style={{ color: filtered ? '#1677ff' : undefined }} />,
  onFilter: (value: any, record: AlertHistoryItem) =>
    String(getValue(record) ?? '').toLowerCase().includes(String(value ?? '').toLowerCase()),
})

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
  const [matchesTotalExact, setMatchesTotalExact] = useState(true)
  const [matchesPage, setMatchesPage] = useState(1)
  const [matchesPageSize, setMatchesPageSize] = useState(10)
  const [matchRuleFilters, setMatchRuleFilters] = useState<Array<{ text: string; value: string }>>([])
  const [selectedSilence, setSelectedSilence] = useState<AlertSilence | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const matchesRequestSeqRef = useRef(0)
  const countRequestSeqRef = useRef(0)
  const [form] = Form.useForm()
  const currentUser = useAuthStore((state) => state.user)
  const navigate = useNavigate()
  const canModify = !currentUser?.read_only

  const fetchData = async (options?: { silent?: boolean }) => {
    if (!options?.silent) {
      setLoading(true)
    }
    try {
      const result = await getAlertSilences({
        include_match_counts: false,
        include_total_match_counts: false,
      })
      const nextItems = result.items.map((item) => ({
        ...item,
        matched_active_alerts: item.matched_active_alerts ?? null,
        matched_total_alerts: item.matched_total_alerts ?? null,
      }))
      setItems(nextItems)
      void loadMatchCounts(nextItems)
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
    return () => {
      countRequestSeqRef.current += 1
    }
  }, [])

  const loadMatchCounts = async (silences: AlertSilence[]) => {
    const requestSeq = countRequestSeqRef.current + 1
    countRequestSeqRef.current = requestSeq
    let targets = silences.filter((item) => item.id)
    const maxRounds = 8
    for (let round = 0; round < maxRounds && targets.length > 0; round += 1) {
      const pendingTargets: AlertSilence[] = []
      for (const silence of targets) {
      if (requestSeq !== countRequestSeqRef.current) return
      try {
        const result = await getAlertSilenceMatchCounts(silence.id)
        if (requestSeq !== countRequestSeqRef.current) return
        setItems((prev) => prev.map((item) => {
          if (item.id !== silence.id) return item
          return {
            ...item,
            matched_active_alerts: result.active.count ?? item.matched_active_alerts ?? null,
            matched_total_alerts: result.total.count ?? item.matched_total_alerts ?? null,
          }
        }))
        if (result.pending || result.active.count === null || result.total.count === null) {
          pendingTargets.push(silence)
        }
      } catch {
        if (requestSeq !== countRequestSeqRef.current) return
        pendingTargets.push(silence)
      }
      await new Promise((resolve) => window.setTimeout(resolve, 350))
      }
      targets = pendingTargets
      if (targets.length > 0) {
        await new Promise((resolve) => window.setTimeout(resolve, Math.min(1500 + round * 500, 5000)))
      }
    }
  }

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
    if (submitting) return
    setSubmitting(true)
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
                matched_active_alerts: null,
                matched_total_alerts: null,
              }
            : item
        )))
        message.success('告警屏蔽已更新')
      } else {
        savedItem = await createAlertSilence(payload)
        setItems((prev) => [
          { ...savedItem, matched_active_alerts: null, matched_total_alerts: null },
          ...prev,
        ])
        message.success('告警屏蔽已创建')
      }
      setModalOpen(false)
    } catch (error: any) {
      if (!error?.errorFields) {
        message.error(error?.response?.data?.detail || '保存失败')
      }
    } finally {
      setSubmitting(false)
    }
  }

  const fetchMatches = async (silence: AlertSilence, nextPage = 1, nextPageSize = matchesPageSize) => {
    const requestSeq = matchesRequestSeqRef.current + 1
    matchesRequestSeqRef.current = requestSeq
    setMatchesLoading(true)
    try {
      const result = await getAlertSilenceMatches(silence.id, {
        skip: (nextPage - 1) * nextPageSize,
        limit: nextPageSize,
        active_only: false,
      })
      if (requestSeq !== matchesRequestSeqRef.current) return
      setMatches(result.items)
      setMatchesTotal(result.total)
      setMatchesTotalExact(result.total_exact !== false)
      setMatchRuleFilters(result.rule_filters || [])
      setMatchesPage(nextPage)
      setMatchesPageSize(nextPageSize)
      setItems((prev) => prev.map((item) => (
        item.id === silence.id
          ? { ...item, matched_total_alerts: result.total }
          : item
      )))
    } catch (error: any) {
      if (requestSeq === matchesRequestSeqRef.current) {
        message.error(error?.response?.data?.detail || '获取命中告警失败')
      }
    } finally {
      if (requestSeq === matchesRequestSeqRef.current) {
        setMatchesLoading(false)
      }
    }
  }

  const openMatches = (record: AlertSilence) => {
    setSelectedSilence(record)
    setMatchesTitle(record.name)
    setMatches([])
    setMatchesTotal(0)
    setMatchesTotalExact(true)
    setMatchRuleFilters([])
    setMatchesPage(1)
    setMatchesOpen(true)
    void fetchMatches(record, 1, matchesPageSize)
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
              const countsLoading = record.matched_active_alerts === null || record.matched_total_alerts === null
              const activeLoading = record.matched_active_alerts === null
              const totalLoading = record.matched_total_alerts === null
              const activeCount = record.matched_active_alerts ?? 0
              const totalCount = record.matched_total_alerts ?? 0
              return (
                <Space size={6}>
                  <Tooltip title="当前仍处于触发、确认、忽略或暂缓状态的命中告警">
                    <Tag color={activeLoading ? 'processing' : activeCount > 0 ? 'red' : 'default'}>
                      {activeLoading ? <Space size={4}><Spin size="small" />当前</Space> : `当前 ${activeCount} 条`}
                    </Tag>
                  </Tooltip>
                  <Tooltip title="历史上匹配过这条屏蔽规则的告警">
                    <Tag color={totalLoading ? 'processing' : totalCount > 0 ? 'blue' : 'default'}>
                      {totalLoading ? <Space size={4}><Spin size="small" />历史</Space> : `历史 ${totalCount} 条`}
                    </Tag>
                  </Tooltip>
                  <Tooltip title={countsLoading ? '命中数量正在后台统计；可先查看已加载明细' : '查看历史命中告警'}>
                    <Button
                      type="text"
                      size="small"
                      icon={<EyeOutlined />}
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
          setSubmitting(false)
        }}
        footer={[
          <Button key="reset" onClick={() => form.resetFields()} disabled={submitting}>
            重置
          </Button>,
          <Button key="submit" type="primary" onClick={handleSubmit} loading={submitting}>
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
                            <Input.TextArea
                              rows={3}
                              placeholder="IP 支持单个、CIDR 或范围（如 10.239.0.1-10.239.0.254）；多个值可用逗号、分号或换行分隔"
                            />
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
        title={(
          <Space size={8}>
            <span>{`${matchesTitle || '告警屏蔽'} - 命中告警明细`}</span>
            {matchesLoading ? (
              <Tag color="processing">正在加载</Tag>
            ) : (
              <Tag color={matchesTotal > 0 ? 'blue' : 'default'}>
                {matchesTotalExact ? `命中 ${matchesTotal} 条` : `已加载 ${matchesTotal}+ 条`}
              </Tag>
            )}
          </Space>
        )}
        width="88vw"
        open={matchesOpen}
        footer={null}
        onCancel={() => {
          matchesRequestSeqRef.current += 1
          setMatchesOpen(false)
          setSelectedSilence(null)
          setMatches([])
          setMatchesTotal(0)
          setMatchesTotalExact(true)
          setMatchRuleFilters([])
          setMatchesLoading(false)
        }}
        destroyOnClose
      >
        {matchesLoading && matches.length === 0 ? (
          <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 12 }}>
            正在统计并加载命中告警，加载完成后会显示命中总数。
          </Typography.Text>
        ) : null}
        <Table<AlertHistoryItem>
          rowKey="id"
          loading={matchesLoading}
          dataSource={matches}
          size="small"
          scroll={{ x: 1500, y: 'calc(100vh - 330px)' }}
          pagination={{
            current: matchesPage,
            pageSize: matchesPageSize,
            total: matchesTotal,
            showSizeChanger: true,
            pageSizeOptions: [10, 20, 50, 100],
            showTotal: (total, range) => `第 ${range[0]}-${range[1]} 条 / ${matchesTotalExact ? `共 ${total} 条` : `已加载 ${total}+ 条`}`,
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
              fixed: 'left',
              sorter: (a: AlertHistoryItem, b: AlertHistoryItem) => compareText(a.alarm_id || a.id, b.alarm_id || b.id),
              ...alertColumnSearch((record) => record.alarm_id || record.id, '搜索 Alarm ID'),
              render: (value: string | null | undefined, record: AlertHistoryItem) => (
                <a onClick={() => navigate(`/alerts/history?alert_id=${record.id}`)}>
                  {value || record.id}
                </a>
              ),
            },
            {
              title: '告警规则',
              dataIndex: 'rule_name',
              width: 200,
              sorter: (a: AlertHistoryItem, b: AlertHistoryItem) => compareText(a.rule_name || a.rule_id, b.rule_name || b.rule_id),
              filters: matchRuleFilters,
              filterSearch: true,
              onFilter: (value: any, record: AlertHistoryItem) => {
                const key = record.rule_id ? String(record.rule_id) : (record.rule_name || '-')
                return key === value
              },
              render: (value?: string | null, record?: AlertHistoryItem) => value || (record?.rule_id ? `规则 ${record.rule_id}` : '-'),
            },
            {
              title: '设备',
              width: 260,
              sorter: (a: AlertHistoryItem, b: AlertHistoryItem) => compareText(a.device_name || a.device_ip, b.device_name || b.device_ip),
              ...alertColumnSearch((record) => `${record.device_name || ''} ${record.device_ip || ''}`, '搜索设备/IP'),
              render: (_: unknown, record: AlertHistoryItem) => (
                <Space direction="vertical" size={0}>
                  <span>{record.device_name || '-'}</span>
                  <Typography.Text type="secondary">{record.device_ip || '-'}</Typography.Text>
                </Space>
              ),
            },
            {
              title: '对象',
              width: 220,
              sorter: (a: AlertHistoryItem, b: AlertHistoryItem) => compareText(a.alert_target_name || a.alert_target_key, b.alert_target_name || b.alert_target_key),
              ...alertColumnSearch((record) => record.alert_target_name || record.alert_target_key, '搜索对象'),
              render: (_: unknown, record: AlertHistoryItem) => record.alert_target_name || record.alert_target_key || '-',
            },
            {
              title: '消息',
              dataIndex: 'message',
              width: 420,
              ellipsis: true,
              sorter: (a: AlertHistoryItem, b: AlertHistoryItem) => compareText(a.message, b.message),
              ...alertColumnSearch((record) => record.message, '搜索消息'),
              render: (value?: string | null) => (
                <Tooltip mouseEnterDelay={0.15} title={<span style={{ whiteSpace: 'pre-wrap' }}>{value || '-'}</span>}>
                  <span>{value || '-'}</span>
                </Tooltip>
              ),
            },
            {
              title: '状态',
              dataIndex: 'status',
              width: 90,
              sorter: (a: AlertHistoryItem, b: AlertHistoryItem) => compareText(statusLabels[a.status] || a.status, statusLabels[b.status] || b.status),
              ...alertColumnSearch((record) => statusLabels[record.status] || record.status, '搜索状态'),
              render: (value: string) => <Tag color={statusColors[value] || 'default'}>{statusLabels[value] || value}</Tag>,
            },
            {
              title: '开始时间',
              dataIndex: 'started_at',
              width: 170,
              sorter: (a: AlertHistoryItem, b: AlertHistoryItem) => dayjs(a.started_at || 0).valueOf() - dayjs(b.started_at || 0).valueOf(),
              ...alertColumnSearch((record) => record.started_at ? new Date(record.started_at).toLocaleString() : '-', '搜索开始时间'),
              render: (value?: string | null) => value ? new Date(value).toLocaleString() : '-',
            },
          ]}
        />
      </Modal>
    </Card>
  )
}

export default AlertSilences
