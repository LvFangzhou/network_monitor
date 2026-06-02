import { useEffect, useMemo, useState } from 'react'
import { Button, Card, Checkbox, Col, Dropdown, Empty, Form, Input, Modal, Popconfirm, Row, Select, Space, Switch, Table, Tag, message, theme } from 'antd'
import { DeleteOutlined, EditOutlined, LineChartOutlined, MinusCircleOutlined, PlusOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { createCustomer, deleteCustomer, getCustomerAudits, getCustomerFlowTraffic, getCustomers, updateCustomer, type Customer, type CustomerAudit, type CustomerFlowPoint } from '../../api/resources'
import { getDatacenters, type Datacenter } from '../../api/devices'
import { useAuthStore } from '../../store/auth'

const TABLE_TEXT_STYLE: React.CSSProperties = {
  display: 'block',
  width: '100%',
  fontSize: 11,
  lineHeight: 1.35,
  fontWeight: 700,
  whiteSpace: 'nowrap',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
}

const PROVIDER_COLOR_PALETTE = [
  { background: '#e6f4ff', border: '#91caff', color: '#0958d9' },
  { background: '#f6ffed', border: '#b7eb8f', color: '#389e0d' },
  { background: '#fff7e6', border: '#ffd591', color: '#d46b08' },
  { background: '#fff0f6', border: '#ffadd2', color: '#c41d7f' },
  { background: '#f9f0ff', border: '#d3adf7', color: '#722ed1' },
  { background: '#e6fffb', border: '#87e8de', color: '#08979c' },
]

const tablePagination = {
  defaultPageSize: 20,
  showSizeChanger: true,
  pageSizeOptions: [10, 20, 50, 100],
  showTotal: (total: number, range: [number, number]) => `第 ${range[0]}-${range[1]} 条 / 共 ${total} 条`,
}

const CUSTOMER_VISIBLE_COLUMNS_KEY = 'customer-list-visible-columns-v2'
const DEFAULT_VISIBLE_COLUMNS = [
  'name',
  'legal_name',
  'customer_sites',
  'customer_resources',
  'service_manager_name',
  'service_manager_contact',
  'sales_name',
  'sales_contact',
  'contact_info',
  'contact_group',
  'status',
]

const FLOW_RANGE_OPTIONS = [
  { label: '过去30分钟', value: '-30m', interval: '30s' },
  { label: '过去1小时', value: '-1h', interval: '1m' },
  { label: '过去6小时', value: '-6h', interval: '5m' },
  { label: '过去24小时', value: '-24h', interval: '15m' },
  { label: '过去3天', value: '-3d', interval: '1h' },
  { label: '过去7天', value: '-7d', interval: '2h' },
]

const formatBps = (value?: number | null) => {
  const numeric = Number(value || 0)
  if (numeric >= 1_000_000_000) return `${(numeric / 1_000_000_000).toFixed(2)} Gbps`
  if (numeric >= 1_000_000) return `${(numeric / 1_000_000).toFixed(1)} Mbps`
  if (numeric >= 1_000) return `${(numeric / 1_000).toFixed(1)} Kbps`
  return `${numeric.toFixed(0)} bps`
}

const chartTimeLabel = (value?: string) => {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
}

const CustomerList = () => {
  const canModify = !useAuthStore((state) => state.user?.read_only)
  const [form] = Form.useForm()
  const [search, setSearch] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [open, setOpen] = useState(false)
  const [customers, setCustomers] = useState<Customer[]>([])
  const [editingCustomer, setEditingCustomer] = useState<Customer | null>(null)
  const [auditModalOpen, setAuditModalOpen] = useState(false)
  const [auditCustomer, setAuditCustomer] = useState<Customer | null>(null)
  const [auditLoading, setAuditLoading] = useState(false)
  const [audits, setAudits] = useState<CustomerAudit[]>([])
  const [flowModalOpen, setFlowModalOpen] = useState(false)
  const [flowCustomer, setFlowCustomer] = useState<Customer | null>(null)
  const [flowLoading, setFlowLoading] = useState(false)
  const [flowRange, setFlowRange] = useState(FLOW_RANGE_OPTIONS[1].value)
  const [flowCidr, setFlowCidr] = useState<string | undefined>()
  const [flowAvailableCidrs, setFlowAvailableCidrs] = useState<string[]>([])
  const [flowData, setFlowData] = useState<CustomerFlowPoint[]>([])
  const [datacenters, setDatacenters] = useState<Datacenter[]>([])
  const [visibleColumns, setVisibleColumns] = useState<string[]>(() => {
    try {
      const stored = window.localStorage.getItem(CUSTOMER_VISIBLE_COLUMNS_KEY)
      const parsed = stored ? JSON.parse(stored) : DEFAULT_VISIBLE_COLUMNS
      return Array.isArray(parsed) ? parsed.filter((item) => item !== 'action') : DEFAULT_VISIBLE_COLUMNS
    } catch {
      return DEFAULT_VISIBLE_COLUMNS
    }
  })
  const navigate = useNavigate()
  const {
    token: { colorBgContainer, colorFillAlter, colorBorder, colorText, colorTextSecondary },
  } = theme.useToken()

  const createEmptySite = () => ({
    datacenter_id: undefined,
    bandwidth_description: '',
    description: '',
    private_network_entries: [{ prefix: '', mask: '' }],
    public_address_entries: [{ prefix: '', mask: '' }],
  })

  const getCustomerPublicCidrs = (customer?: Customer | null) => {
    const cidrs: string[] = []
    customer?.customer_sites?.forEach((site) => {
      site.public_address_entries?.forEach((entry) => {
        if (entry.cidr) {
          cidrs.push(entry.cidr)
        }
      })
    })
    customer?.public_address_entries?.forEach((entry) => {
      if (entry.cidr) {
        cidrs.push(entry.cidr)
      }
    })
    return Array.from(new Set(cidrs))
  }

  const renderCidrsHighlight = (entries?: Customer['private_network_entries']) => {
    if (!entries?.length) {
      return <span style={{ color: '#999' }}>-</span>
    }
    return (
      <Space size={[6, 6]} wrap>
        {entries.map((entry, index) => (
          <span
            key={`${entry.cidr}-${index}`}
            style={{
              display: 'inline-block',
              padding: '1px 6px',
              borderRadius: 4,
              background: '#e6f4ff',
              border: '1px solid #91caff',
              color: '#0958d9',
              fontWeight: 800,
              fontSize: 11,
              lineHeight: 1.35,
            }}
          >
            {entry.cidr}
          </span>
        ))}
      </Space>
    )
  }

  const getProviderColors = (providerName?: string) => {
    if (!providerName) {
      return {
        background: colorFillAlter,
        border: colorBorder,
        color: colorTextSecondary,
      }
    }
    const seed = Array.from(providerName).reduce((sum, char) => sum + char.charCodeAt(0), 0)
    return PROVIDER_COLOR_PALETTE[seed % PROVIDER_COLOR_PALETTE.length]
  }

  const renderPublicAddresses = (entries?: Customer['public_address_entries']) => {
    if (!entries?.length) {
      return <span style={{ color: '#999' }}>-</span>
    }
    return (
      <Space direction="vertical" size={6} style={{ width: '100%', alignItems: 'flex-start' }}>
        {entries.map((entry, index) => {
          const colors = getProviderColors(entry.provider_name)
          return (
            <span
              key={`${entry.cidr}-${entry.provider_name || 'unknown'}-${index}`}
              title={entry.matched_circuit_name ? `${entry.cidr}-${entry.provider_name || '未匹配'}（来源：${entry.matched_circuit_name}）` : `${entry.cidr}-${entry.provider_name || '未匹配'}`}
              style={{
                display: 'inline-block',
                maxWidth: '100%',
                padding: '1px 6px',
                borderRadius: 4,
                background: colors.background,
                border: `1px solid ${colors.border}`,
                color: colors.color,
                fontWeight: 800,
                fontSize: 11,
                lineHeight: 1.35,
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}
            >
              {entry.cidr}-{entry.provider_name || '未匹配运营商'}
            </span>
          )
        })}
      </Space>
    )
  }

  const fetchCustomers = async (keyword = search) => {
    setLoading(true)
    try {
      const result = await getCustomers(keyword || undefined)
      setCustomers(result.items)
    } catch {
      message.error('获取客户列表失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchCustomers('')
    getDatacenters()
      .then(setDatacenters)
      .catch(() => message.error('获取机房列表失败'))
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      fetchCustomers(search)
    }, 250)
    return () => window.clearTimeout(timer)
  }, [search])

  useEffect(() => {
    window.localStorage.setItem(CUSTOMER_VISIBLE_COLUMNS_KEY, JSON.stringify(visibleColumns))
  }, [visibleColumns])

  const formatDateTime = (value?: string) => {
    if (!value) return '-'
    const date = new Date(value)
    if (Number.isNaN(date.getTime())) return value
    return date.toLocaleString('zh-CN', { hour12: false })
  }

  const formatAuditValue = (field: string, value: unknown) => {
    if (value === null || value === undefined || value === '') {
      return '空'
    }
    if (field === 'is_active') {
      return value ? '启用' : '停用'
    }
    if (field === 'customer_sites' && Array.isArray(value)) {
      return `${value.length}个机房资源`
    }
    if (typeof value === 'object') {
      return JSON.stringify(value)
    }
    return String(value)
  }

  const customerAuditFieldLabelMap: Record<string, string> = {
    name: '客户名称',
    legal_name: '真实名称',
    customer_sites: '机房资源',
    private_networks: '使用内网网段',
    public_addresses: '使用公网地址',
    bandwidth_description: '使用带宽',
    dedicated_lines: '专线资源',
    service_manager_name: '服务经理',
    service_manager_contact: '服务经理联系方式',
    sales_name: '销售',
    sales_contact: '销售联系方式',
    contact_info: '客户联系方式',
    contact_group: '客户联系群',
    description: '备注',
    is_active: '状态',
  }

  const renderAuditDescription = (audit: CustomerAudit) => {
    const when = formatDateTime(audit.created_at)
    if (!audit.change_summary?.length) {
      return (
        <span>
          账号 <span style={{ color: '#1677ff', fontWeight: 700 }}>【{audit.actor_username}】</span>
          {' '}在 {when} 执行了{audit.action === 'create' ? '新增' : audit.action === 'delete' ? '删除' : '修改'}操作。
        </span>
      )
    }
    return audit.change_summary.map((item, index) => {
      const label = customerAuditFieldLabelMap[item.field] || item.field
      const beforeValue = formatAuditValue(item.field, item.before)
      const afterValue = formatAuditValue(item.field, item.after)
      if (audit.action === 'create') {
        return (
          <div key={`${audit.id}-${item.field}-${index}`}>
            账号 <span style={{ color: '#1677ff', fontWeight: 700 }}>【{audit.actor_username}】</span>
            {' '}在 {when} 新增了【{label}】，内容为 <span style={{ color: '#389e0d', fontWeight: 700 }}>{afterValue}</span>。
          </div>
        )
      }
      if (audit.action === 'delete') {
        return (
          <div key={`${audit.id}-${item.field}-${index}`}>
            账号 <span style={{ color: '#1677ff', fontWeight: 700 }}>【{audit.actor_username}】</span>
            {' '}在 {when} 删除了【{label}】，删除前内容为 <span style={{ color: '#cf1322', fontWeight: 700 }}>{beforeValue}</span>。
          </div>
        )
      }
      return (
        <div key={`${audit.id}-${item.field}-${index}`}>
          账号 <span style={{ color: '#1677ff', fontWeight: 700 }}>【{audit.actor_username}】</span>
          {' '}在 {when} 将【{label}】 <span style={{ color: '#cf1322', fontWeight: 700 }}>{beforeValue}</span> 修改为 <span style={{ color: '#389e0d', fontWeight: 700 }}>{afterValue}</span>。
        </div>
      )
    })
  }

  const handleCreate = () => {
    setEditingCustomer(null)
    form.resetFields()
    form.setFieldsValue({
      is_active: true,
      customer_sites: [createEmptySite()],
    })
    setOpen(true)
  }

  const handleEdit = (record: Customer) => {
    setEditingCustomer(record)
    form.setFieldsValue({
      ...record,
      customer_sites: record.customer_sites?.length
        ? record.customer_sites.map((site) => ({
            datacenter_id: site.datacenter_id,
            bandwidth_description: site.bandwidth_description,
            description: site.description,
            private_network_entries: site.private_network_entries?.length
              ? site.private_network_entries.map((item) => ({ prefix: item.prefix, mask: item.mask }))
              : [{ prefix: '', mask: '' }],
            public_address_entries: site.public_address_entries?.length
              ? site.public_address_entries.map((item) => ({ prefix: item.prefix, mask: item.mask }))
              : [{ prefix: '', mask: '' }],
          }))
        : [createEmptySite()],
    })
    setOpen(true)
  }

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields()
      const customerSites = (values.customer_sites || []).map((site: any) => ({
        datacenter_id: site.datacenter_id,
        bandwidth_description: site.bandwidth_description,
        description: site.description,
        private_network_entries: (site.private_network_entries || [])
          .filter((item: any) => item?.prefix)
          .map((item: any) => ({
            prefix: item.prefix,
            mask: item.mask || '',
            cidr: item.mask ? `${item.prefix}/${item.mask}` : item.prefix,
          })),
        public_address_entries: (site.public_address_entries || [])
          .filter((item: any) => item?.prefix)
          .map((item: any) => ({
            prefix: item.prefix,
            mask: item.mask || '',
            cidr: item.mask ? `${item.prefix}/${item.mask}` : item.prefix,
          })),
      }))
      const payload = {
        ...values,
        customer_sites: customerSites,
      }
      setSaving(true)
      if (editingCustomer) {
        await updateCustomer(editingCustomer.id, payload)
        message.success('更新成功')
      } else {
        await createCustomer(payload)
        message.success('创建成功')
      }
      setOpen(false)
      fetchCustomers()
    } catch (error: any) {
      if (!error?.errorFields) {
        message.error(error?.response?.data?.detail || '保存失败')
      }
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (id: number) => {
    try {
      await deleteCustomer(id)
      message.success('删除成功')
      fetchCustomers()
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '删除失败')
    }
  }

  const openAuditModal = async (record: Customer) => {
    setAuditCustomer(record)
    setAuditModalOpen(true)
    setAuditLoading(true)
    try {
      const result = await getCustomerAudits(record.id)
      setAudits(result.items)
    } catch {
      message.error('获取客户更改记录失败')
    } finally {
      setAuditLoading(false)
    }
  }

  const fetchFlowTraffic = async (customer: Customer, cidrValue = flowCidr, rangeValue = flowRange) => {
    setFlowLoading(true)
    try {
      const selectedRange = FLOW_RANGE_OPTIONS.find((item) => item.value === rangeValue) || FLOW_RANGE_OPTIONS[1]
      const result = await getCustomerFlowTraffic(customer.id, {
        range: selectedRange.value,
        interval: selectedRange.interval,
        cidr: cidrValue,
      })
      setFlowAvailableCidrs(result.available_cidrs || [])
      setFlowData(result.data || [])
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '获取客户IP流量失败')
      setFlowData([])
    } finally {
      setFlowLoading(false)
    }
  }

  const openFlowModal = async (record: Customer) => {
    const cidrs = getCustomerPublicCidrs(record)
    const defaultCidr = cidrs[0]
    setFlowCustomer(record)
    setFlowAvailableCidrs(cidrs)
    setFlowCidr(defaultCidr)
    setFlowRange(FLOW_RANGE_OPTIONS[1].value)
    setFlowModalOpen(true)
    await fetchFlowTraffic(record, defaultCidr, FLOW_RANGE_OPTIONS[1].value)
  }

  const handleFlowRangeChange = async (value: string) => {
    setFlowRange(value)
    if (flowCustomer) {
      await fetchFlowTraffic(flowCustomer, flowCidr, value)
    }
  }

  const handleFlowCidrChange = async (value: string) => {
    const nextCidr = value || undefined
    setFlowCidr(nextCidr)
    if (flowCustomer) {
      await fetchFlowTraffic(flowCustomer, nextCidr, flowRange)
    }
  }

  const renderSiteBlocks = (record: Customer) => {
    if (!record.customer_sites?.length) {
      return <span style={{ color: '#999', fontSize: 11 }}>未配置机房资源</span>
    }
    return (
      <Space direction="vertical" size={6} style={{ width: '100%' }}>
        {record.customer_sites.map((site, index) => (
          <div
            key={`${record.id}-${site.datacenter_id || index}`}
            style={{
              border: `1px solid ${colorBorder}`,
              borderRadius: 6,
              padding: '5px 6px',
              background: colorBgContainer,
            }}
          >
            <div style={{ fontSize: 11, fontWeight: 800, color: colorText, marginBottom: 4 }}>
              {site.datacenter_name || '未选择机房'}
            </div>
            <div style={{ marginBottom: 4 }}>
              <span style={{ fontSize: 10, fontWeight: 700, color: colorTextSecondary, marginRight: 6 }}>内网</span>
              {renderCidrsHighlight(site.private_network_entries)}
            </div>
            <div style={{ marginBottom: 4 }}>
              <span style={{ fontSize: 10, fontWeight: 700, color: colorTextSecondary, marginRight: 6 }}>公网</span>
              {renderPublicAddresses(site.public_address_entries)}
            </div>
            <div style={{ fontSize: 10, fontWeight: 700, color: colorTextSecondary }}>
              带宽：{site.bandwidth_description || '-'}
            </div>
          </div>
        ))}
      </Space>
    )
  }

  const allColumns = useMemo(() => ([
    { title: '客户名称', dataIndex: 'name', key: 'name', width: 140, render: (v: string) => <span style={TABLE_TEXT_STYLE}>{v || '-'}</span> },
    { title: '真实名称', dataIndex: 'legal_name', key: 'legal_name', width: 180, render: (v: string) => <span style={TABLE_TEXT_STYLE}>{v || '-'}</span> },
    {
      title: '机房资源',
      dataIndex: 'customer_sites',
      key: 'customer_sites',
      width: 260,
      render: (_: unknown, record: Customer) => renderSiteBlocks(record),
    },
    {
      title: '专线资源',
      dataIndex: 'customer_resources',
      key: 'customer_resources',
      width: 220,
      render: (_: unknown, record: Customer) => {
        if (!record.customer_resources?.length) {
          return <span style={{ color: '#999', fontSize: 11 }}>未关联专线</span>
        }
        return (
          <Space size={[6, 6]} wrap>
            {record.customer_resources.map((resource) => (
              <Button
                key={resource.id}
                type="link"
                style={{ paddingInline: 0, fontSize: 11, fontWeight: 700, height: 'auto' }}
                onClick={() => navigate('/private-circuits', { state: { focusCircuitId: resource.id } })}
              >
                {(resource.datacenter_name || '未分配机房') + '-【' + resource.name + '】'}
              </Button>
            ))}
          </Space>
        )
      },
    },
    { title: '服务经理', dataIndex: 'service_manager_name', key: 'service_manager_name', width: 110, render: (v: string) => <span style={TABLE_TEXT_STYLE}>{v || '-'}</span> },
    { title: '服务经理联系方式', dataIndex: 'service_manager_contact', key: 'service_manager_contact', width: 150, render: (v: string) => <span style={TABLE_TEXT_STYLE}>{v || '-'}</span> },
    { title: '销售', dataIndex: 'sales_name', key: 'sales_name', width: 100, render: (v: string) => <span style={TABLE_TEXT_STYLE}>{v || '-'}</span> },
    { title: '销售联系方式', dataIndex: 'sales_contact', key: 'sales_contact', width: 150, render: (v: string) => <span style={TABLE_TEXT_STYLE}>{v || '-'}</span> },
    { title: '客户联系方式', dataIndex: 'contact_info', key: 'contact_info', width: 150, render: (v: string) => <span style={TABLE_TEXT_STYLE}>{v || '-'}</span> },
    { title: '客户联系群', dataIndex: 'contact_group', key: 'contact_group', width: 150, render: (v: string) => <span style={TABLE_TEXT_STYLE}>{v || '-'}</span> },
    {
      title: '状态',
      dataIndex: 'is_active',
      key: 'is_active',
      width: 90,
      render: (value: boolean) => <Tag style={{ fontWeight: 700 }} color={value ? 'success' : 'default'}>{value ? '启用' : '停用'}</Tag>,
    },
    {
      title: '操作',
      key: 'action',
      width: 220,
      render: (_: unknown, record: Customer) => (
        <Space size={4}>
          <Button
            type="link"
            style={{ paddingInline: 2, fontSize: 12, fontWeight: 700 }}
            icon={<LineChartOutlined />}
            disabled={getCustomerPublicCidrs(record).length === 0}
            onClick={() => openFlowModal(record)}
          >
            IP流量
          </Button>
          <Button type="link" style={{ paddingInline: 2, fontSize: 12, fontWeight: 700 }} onClick={() => openAuditModal(record)}>查看更改记录</Button>
          {canModify ? (
            <>
              <Button type="text" icon={<EditOutlined />} onClick={() => handleEdit(record)} />
              <Popconfirm title="确认删除该客户吗？" onConfirm={() => handleDelete(record.id)}>
                <Button type="text" danger icon={<DeleteOutlined />} />
              </Popconfirm>
            </>
          ) : null}
        </Space>
      ),
    },
  ]), [customers, datacenters])

  const columns = useMemo(
    () => allColumns.filter((column) => String(column.key) === 'action' || visibleColumns.includes(String(column.key))),
    [allColumns, visibleColumns],
  )

  const flowChartData = useMemo(() => (
    flowData.map((item) => ({
      ...item,
      timeLabel: chartTimeLabel(item._time),
      in_bps: Number(item.in_bps || 0),
      out_bps: Number(item.out_bps || 0),
    }))
  ), [flowData])

  return (
    <Card
      title="客户管理"
      extra={(
        <Space>
          <Input
            allowClear
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="搜索客户名称、真实名称、网段、IP、联系方式"
            style={{ width: 320 }}
          />
          <Dropdown
            trigger={['click']}
            menu={{
              items: [
                {
                  key: 'visible-columns',
                  label: (
                    <Checkbox.Group
                      value={visibleColumns}
                      onChange={(values) => setVisibleColumns(values as string[])}
                      style={{ display: 'grid', rowGap: 8 }}
                    >
                      {allColumns
                        .filter((column) => !['action'].includes(String(column.key)))
                        .map((column) => (
                          <Checkbox key={String(column.key)} value={String(column.key)}>
                            {String(column.title)}
                          </Checkbox>
                        ))}
                    </Checkbox.Group>
                  ),
                },
              ],
            }}
          >
            <Button>显示/隐藏列</Button>
          </Dropdown>
          {canModify ? <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>新增客户</Button> : null}
        </Space>
      )}
    >
      <style>{`
        .customer-network-card .ant-card-head {
          min-height: 34px;
          padding: 0 12px;
        }
        .customer-network-card .ant-card-head-title {
          padding: 8px 0;
          font-size: 13px;
          font-weight: 700;
        }
        .customer-network-card .ant-card-body {
          padding: 10px;
        }
        .customer-network-card .ant-form-item {
          margin-bottom: 0;
        }
        .customer-network-card .ant-form-item-label > label {
          font-size: 12px;
          font-weight: 700;
        }
        .customer-network-inline-header {
          display: grid;
          grid-template-columns: 190px 88px 64px;
          column-gap: 10px;
          align-items: center;
          margin-bottom: 6px;
          padding: 0 2px;
        }
        .customer-network-inline-header span {
          font-size: 12px;
          font-weight: 700;
          color: ${colorText};
        }
        .customer-network-inline-row {
          display: grid;
          grid-template-columns: 190px 88px 64px;
          column-gap: 10px;
          align-items: center;
          margin-bottom: 8px;
        }
        .customer-network-inline-row:last-child {
          margin-bottom: 0;
        }
        .customer-network-inline-row .ant-form-item {
          margin-bottom: 0;
        }
        .customer-network-inline-actions {
          display: flex;
          justify-content: flex-end;
          gap: 6px;
        }
        .customer-table .ant-table-thead > tr > th {
          background: ${colorBgContainer};
          font-size: 12px;
          font-weight: 800;
          padding: 10px 12px;
        }
        .customer-table .ant-table-tbody > tr > td {
          padding: 12px;
          font-size: 11px;
          font-weight: 700;
          vertical-align: middle;
        }
        .customer-table .ant-table-cell { white-space: nowrap; }
        .customer-table .ant-table-tbody > tr > td:nth-child(3) { white-space: normal; }
        .customer-resource-column {
          width: 100%;
        }
      `}</style>
      <Table
        className="customer-table"
        rowKey="id"
        loading={loading}
        dataSource={customers}
        scroll={{ x: 1420 }}
        columns={columns}
        pagination={tablePagination}
      />

      <Modal
        title={editingCustomer ? '编辑客户' : '新增客户'}
        open={open}
        onCancel={() => setOpen(false)}
        onOk={handleSubmit}
        confirmLoading={saving}
        width={900}
        destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Row gutter={16}>
            <Col span={12}>
              <Form.Item name="name" label="客户名称" rules={[{ required: true, message: '请输入客户名称' }]}>
                <Input />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="legal_name" label="真实名称">
                <Input />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="service_manager_name" label="服务经理">
                <Input />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="service_manager_contact" label="服务经理联系方式">
                <Input />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="sales_name" label="销售">
                <Input />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="sales_contact" label="销售联系方式">
                <Input />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.List name="customer_sites">
                {(siteFields, { add: addSite, remove: removeSite }) => (
                  <Space direction="vertical" style={{ width: '100%' }} size="middle">
                    {siteFields.map((siteField, siteIndex) => (
                      <Card
                        key={siteField.key}
                        size="small"
                        title={`机房资源 ${siteIndex + 1}`}
                        className="customer-network-card"
                        extra={siteFields.length > 1 ? <Button type="text" danger icon={<MinusCircleOutlined />} onClick={() => removeSite(siteField.name)} /> : null}
                      >
                        <Row gutter={12}>
                          <Col span={10}>
                            <Form.Item
                              name={[siteField.name, 'datacenter_id']}
                              label="机房"
                              rules={[{ required: true, message: '请选择机房' }]}
                            >
                              <Select
                                showSearch
                                optionFilterProp="label"
                                placeholder="请选择机房"
                                options={datacenters.map((item) => ({
                                  value: item.id,
                                  label: item.code ? `${item.name} (${item.code})` : item.name,
                                }))}
                              />
                            </Form.Item>
                          </Col>
                          <Col span={8}>
                            <Form.Item name={[siteField.name, 'bandwidth_description']} label="机房使用带宽">
                              <Input placeholder="例如：2G" />
                            </Form.Item>
                          </Col>
                          <Col span={24}>
                            <Card size="small" title="内网网段" className="customer-network-card">
                              <Form.List name={[siteField.name, 'private_network_entries']}>
                                {(fields, { add, remove }) => (
                                  <Space direction="vertical" style={{ width: '100%' }} size="small">
                                    <div className="customer-network-inline-header">
                                      <span>前缀</span>
                                      <span>掩码</span>
                                      <span>操作</span>
                                    </div>
                                    {fields.map((field) => (
                                      <div key={field.key} className="customer-network-inline-row">
                                        <Form.Item {...field} name={[field.name, 'prefix']} rules={[{ required: true, message: '请输入前缀' }]}>
                                          <Input placeholder="例如：10.158.0.0" />
                                        </Form.Item>
                                        <Form.Item {...field} name={[field.name, 'mask']}>
                                          <Input placeholder="例如：16" />
                                        </Form.Item>
                                        <div className="customer-network-inline-actions">
                                          {fields.length > 1 ? (
                                            <Button type="text" danger icon={<MinusCircleOutlined />} onClick={() => remove(field.name)} />
                                          ) : <span />}
                                          <Button type="text" icon={<PlusOutlined />} onClick={() => add({ prefix: '', mask: '' })} />
                                        </div>
                                      </div>
                                    ))}
                                  </Space>
                                )}
                              </Form.List>
                            </Card>
                          </Col>
                          <Col span={24}>
                            <Card size="small" title="公网地址" className="customer-network-card">
                              <Form.List name={[siteField.name, 'public_address_entries']}>
                                {(fields, { add, remove }) => (
                                  <Space direction="vertical" style={{ width: '100%' }} size="small">
                                    <div className="customer-network-inline-header">
                                      <span>前缀/IP</span>
                                      <span>掩码</span>
                                      <span>操作</span>
                                    </div>
                                    {fields.map((field) => (
                                      <div key={field.key} className="customer-network-inline-row">
                                        <Form.Item {...field} name={[field.name, 'prefix']} rules={[{ required: true, message: '请输入前缀或IP' }]}>
                                          <Input placeholder="例如：36.103.199.0" />
                                        </Form.Item>
                                        <Form.Item {...field} name={[field.name, 'mask']}>
                                          <Input placeholder="例如：24" />
                                        </Form.Item>
                                        <div className="customer-network-inline-actions">
                                          {fields.length > 1 ? (
                                            <Button type="text" danger icon={<MinusCircleOutlined />} onClick={() => remove(field.name)} />
                                          ) : <span />}
                                          <Button type="text" icon={<PlusOutlined />} onClick={() => add({ prefix: '', mask: '' })} />
                                        </div>
                                      </div>
                                    ))}
                                  </Space>
                                )}
                              </Form.List>
                            </Card>
                          </Col>
                          <Col span={24}>
                            <Form.Item name={[siteField.name, 'description']} label="机房资源备注">
                              <Input placeholder="例如：该机房客户资源说明" />
                            </Form.Item>
                          </Col>
                        </Row>
                      </Card>
                    ))}
                    <Button type="dashed" block icon={<PlusOutlined />} onClick={() => addSite(createEmptySite())}>
                      新增机房资源
                    </Button>
                  </Space>
                )}
              </Form.List>
            </Col>
            <Col span={8}>
              <Form.Item name="contact_info" label="客户联系方式">
                <Input placeholder="例如：张三 / 138xxxx / 邮箱" />
              </Form.Item>
            </Col>
            <Col span={8}>
              <Form.Item name="contact_group" label="客户联系群">
                <Input placeholder="例如：企业微信 / 钉钉 / 飞书群信息" />
              </Form.Item>
            </Col>
            <Col span={24}>
              <Form.Item name="description" label="备注">
                <Input.TextArea rows={3} />
              </Form.Item>
            </Col>
            <Col span={24}>
              <Form.Item name="is_active" label="启用状态" valuePropName="checked">
                <Switch />
              </Form.Item>
            </Col>
          </Row>
        </Form>
      </Modal>

      <Modal
        title={flowCustomer ? `${flowCustomer.name} - 公网IP流量` : '公网IP流量'}
        open={flowModalOpen}
        onCancel={() => setFlowModalOpen(false)}
        footer={null}
        width={980}
      >
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Space wrap>
            <Select
              value={flowCidr}
              style={{ width: 260 }}
              placeholder="选择公网IP/CIDR"
              options={flowAvailableCidrs.map((item) => ({ label: item, value: item }))}
              onChange={handleFlowCidrChange}
            />
            <Select
              value={flowRange}
              style={{ width: 150 }}
              options={FLOW_RANGE_OPTIONS.map((item) => ({ label: item.label, value: item.value }))}
              onChange={handleFlowRangeChange}
            />
            <Button
              loading={flowLoading}
              onClick={() => flowCustomer && fetchFlowTraffic(flowCustomer, flowCidr, flowRange)}
            >
              刷新
            </Button>
            <Tag color={flowData.length ? 'green' : 'default'}>{flowData.length ? `${flowData.length} 个采样点` : '暂无Flow数据'}</Tag>
          </Space>
          {flowChartData.length ? (
            <div style={{ height: 360, border: `1px solid ${colorBorder}`, borderRadius: 6, padding: 12 }}>
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={flowChartData} margin={{ top: 12, right: 24, left: 12, bottom: 8 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                  <XAxis dataKey="timeLabel" minTickGap={28} />
                  <YAxis tickFormatter={formatBps} width={78} />
                  <Tooltip
                    formatter={(value: number, name: string) => [formatBps(value), name === 'in_bps' ? '入流量' : '出流量']}
                    labelFormatter={(label) => `时间：${label}`}
                  />
                  <Legend formatter={(value) => (value === 'in_bps' ? '入流量' : '出流量')} />
                  <Line type="linear" dataKey="in_bps" stroke="#52c41a" dot={false} strokeWidth={1.5} isAnimationActive={false} />
                  <Line type="linear" dataKey="out_bps" stroke="#fadb14" dot={false} strokeWidth={1.5} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description="暂无该公网IP的Flow流量数据，请确认交换机已向本服务器导出 sFlow/NetFlow"
            />
          )}
        </Space>
      </Modal>

      <Modal
        title={auditCustomer ? `${auditCustomer.name} - 完整更改记录` : '完整更改记录'}
        open={auditModalOpen}
        onCancel={() => setAuditModalOpen(false)}
        footer={null}
        width={920}
      >
        {auditLoading ? (
          <div style={{ padding: '24px 0', textAlign: 'center', color: '#666' }}>加载中...</div>
        ) : audits.length ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            {audits.map((audit) => (
              <div key={audit.id} style={{ border: `1px solid ${colorBorder}`, borderRadius: 8, padding: 12, background: colorBgContainer }}>
                <div style={{ fontSize: 12, fontWeight: 700, color: colorTextSecondary, marginBottom: 8 }}>
                  {audit.actor_username} / {formatDateTime(audit.created_at)}
                </div>
                <div style={{ fontSize: 13, lineHeight: 1.8, fontWeight: 600 }}>
                  {renderAuditDescription(audit)}
                </div>
              </div>
            ))}
          </Space>
        ) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无更改记录" />
        )}
      </Modal>
    </Card>
  )
}

export default CustomerList
