import { useEffect, useRef, useState, type ChangeEvent, type MouseEvent as ReactMouseEvent } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import {
  Table,
  Button,
  Input,
  Space,
  Card,
  Tooltip,
  Popconfirm,
  message,
  Typography,
  Dropdown,
  Checkbox,
  Modal,
  Form,
  Select,
  theme,
} from 'antd'
import {
  PlusOutlined,
  SearchOutlined,
  EditOutlined,
  DeleteOutlined,
  EyeOutlined,
  ExportOutlined,
  DownloadOutlined,
  ImportOutlined,
  SettingOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import { getDevices, deleteDevice, batchDeleteDevices, batchUpdateDevices, exportDevices, exportDeviceTemplate, importDevices, getDeviceFilterOptions } from '../../api/devices'
import type { Device } from '../../api/devices'
import { useAuthStore } from '../../store/auth'

const { Search } = Input
const { Text } = Typography
const { Option } = Select
const DEVICE_LIST_STORAGE_KEY = 'resource-network-device-list-state'
const DEVICE_LIST_STORAGE_VERSION = 6
const COLUMN_FILTER_KEYS = ['name', 'ip_address', 'status', 'is_monitored', 'datacenter', 'model', 'device_type', 'serial_number'] as const
type ColumnFilterKey = typeof COLUMN_FILTER_KEYS[number]
type SortField = ColumnFilterKey
type SortOrder = 'ascend' | 'descend' | undefined
const DEFAULT_VISIBLE_COLUMNS = [
  'name',
  'ip_address',
  'status',
  'is_monitored',
  'datacenter',
  'model',
  'device_type',
  'serial_number',
  'action',
]
const DEFAULT_COLUMN_WIDTHS = {
  name: 180,
  status: 120,
  is_monitored: 110,
  ip_address: 84,
  datacenter: 180,
  device_type: 140,
  device_role: 130,
  vendor: 120,
  model: 160,
  serial_number: 180,
  action: 150,
}
const COLUMN_ORDER = ['name', 'ip_address', 'status', 'is_monitored', 'datacenter', 'device_role', 'model', 'device_type', 'vendor', 'serial_number', 'action']
const TABLE_CELL_TEXT_STYLE: React.CSSProperties = {
  display: 'block',
  width: '100%',
  fontSize: 12,
  lineHeight: 1.45,
  whiteSpace: 'nowrap',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
}

const ipToParts = (value?: string | null) => {
  const parts = String(value || '').trim().split('.')
  if (parts.length !== 4) return null
  const numbers = parts.map((part) => Number(part))
  if (numbers.some((item) => !Number.isInteger(item) || item < 0 || item > 255)) return null
  return numbers
}

const compareIpAddress = (left?: string | null, right?: string | null) => {
  const leftParts = ipToParts(left)
  const rightParts = ipToParts(right)
  if (leftParts && rightParts) {
    for (let index = 0; index < 4; index += 1) {
      if (leftParts[index] !== rightParts[index]) {
        return leftParts[index] - rightParts[index]
      }
    }
    return 0
  }
  if (leftParts) return -1
  if (rightParts) return 1
  return String(left || '').localeCompare(String(right || ''), undefined, { numeric: true, sensitivity: 'base' })
}

const getSearchMode = (value: string): 'fuzzy' | 'regex' => {
  const trimmed = value.trim()
  if (!trimmed) return 'fuzzy'

  try {
    new RegExp(trimmed)
    const regexSignals = ['^', '$', '.*', '.+', '\\d', '\\w', '[', ']', '(', ')', '|', '?', '+', '{', '}']
    return regexSignals.some((signal) => trimmed.includes(signal)) ? 'regex' : 'fuzzy'
  } catch {
    return 'fuzzy'
  }
}

const normalizeVisibleColumns = (value?: string[]) => {
  const nextColumns = Array.isArray(value) ? [...value] : [...DEFAULT_VISIBLE_COLUMNS]

  if (!nextColumns.includes('action')) {
    nextColumns.push('action')
  }

  return COLUMN_ORDER.filter((key) => nextColumns.includes(key))
}

const ResizableHeaderCell = ({
  onResizeStart,
  resizeHandleSide = 'right',
  children,
  style,
  ...restProps
}: any) => (
  <th
    {...restProps}
    style={{
      ...style,
      position: 'relative',
    }}
  >
    {children}
    {onResizeStart ? (
      <span
        onMouseDown={onResizeStart}
        style={{
          position: 'absolute',
          top: 0,
          [resizeHandleSide]: -4,
          width: 8,
          height: '100%',
          cursor: 'col-resize',
          userSelect: 'none',
          zIndex: 1,
        }}
      />
    ) : null}
  </th>
)

const DeviceList = () => {
  const persistedState = (() => {
    try {
      const raw = localStorage.getItem(DEVICE_LIST_STORAGE_KEY)
      if (!raw) {
        return null
      }

      const parsed = JSON.parse(raw)
      return parsed?.version === DEVICE_LIST_STORAGE_VERSION ? parsed : null
    } catch {
      return null
    }
  })()
  const initialVisibleColumns = normalizeVisibleColumns(persistedState?.visibleColumns)

  const [devices, setDevices] = useState<Device[]>([])
  const [loading, setLoading] = useState(false)
  const [total, setTotal] = useState(0)
  const [currentPage, setCurrentPage] = useState(persistedState?.currentPage || 1)
  const [pageSize, setPageSize] = useState(persistedState?.pageSize || 20)
  const [searchKeyword, setSearchKeyword] = useState(persistedState?.searchKeyword || '')
  const [statusFilter, setStatusFilter] = useState<string | undefined>(persistedState?.statusFilter)
  const [roleFilter, setRoleFilter] = useState<string | undefined>(persistedState?.roleFilter)
  const [vendorFilter, setVendorFilter] = useState<string | undefined>(persistedState?.vendorFilter)
  const [monitoredFilter, setMonitoredFilter] = useState<string | undefined>(persistedState?.monitoredFilter)
  const [datacenterFilter, setDatacenterFilter] = useState<number | undefined>(persistedState?.datacenterFilter)
  const [deviceTypeFilter, setDeviceTypeFilter] = useState<number | undefined>(persistedState?.deviceTypeFilter)
  const [columnFilters, setColumnFilters] = useState<Partial<Record<ColumnFilterKey, string>>>(persistedState?.columnFilters || {})
  const [sortField, setSortField] = useState<SortField | undefined>(persistedState?.sortField)
  const [sortOrder, setSortOrder] = useState<SortOrder>(persistedState?.sortOrder)
  const [columnWidths, setColumnWidths] = useState<Record<string, number>>({
    ...DEFAULT_COLUMN_WIDTHS,
    ...(persistedState?.columnWidths || {}),
  })
  const [visibleColumns, setVisibleColumns] = useState<string[]>(initialVisibleColumns)
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([])
  const [batchDeleting, setBatchDeleting] = useState(false)
  const [batchEditVisible, setBatchEditVisible] = useState(false)
  const [batchUpdating, setBatchUpdating] = useState(false)
  const [importing, setImporting] = useState(false)
  const [filterOptions, setFilterOptions] = useState({
    datacenters: [] as Array<{ id: number; name: string; code?: string; location?: string; contact_person?: string }>,
    device_types: [] as Array<{ id: number; name: string; display_name?: string }>,
    device_roles: [] as string[],
    vendors: [] as string[],
    business_types: [] as string[],
    statuses: ['active', 'inactive', 'in_stock', 'deployed'],
  })

  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const canModify = !useAuthStore((state) => state.user?.read_only)
  const {
    token: { colorBgContainer, colorBgElevated, colorBorder, colorText, colorTextSecondary },
  } = theme.useToken()
  const [batchEditForm] = Form.useForm()
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const searchTimerRef = useRef<number | null>(null)
  const resizeStateRef = useRef<{ key: string; startX: number; startWidth: number } | null>(null)

  const getColumnFilterParams = (filters: Partial<Record<ColumnFilterKey, string>>) => ({
    name_text: filters.name || undefined,
    ip_address_text: filters.ip_address || undefined,
    status_text: filters.status || undefined,
    monitored_text: filters.is_monitored || undefined,
    datacenter_text: filters.datacenter || undefined,
    model_text: filters.model || undefined,
    device_type_text: filters.device_type || undefined,
    serial_number_text: filters.serial_number || undefined,
  })

  const fetchDevices = async (params?: any) => {
    const silent = Boolean(params?.silent)
    if (!silent) {
      setLoading(true)
    }
    try {
      const effectiveSearch = (params?.search ?? searchKeyword) || undefined
      const effectiveColumnFilters = params?.columnFilters ?? columnFilters
      const requestOverrides = { ...(params || {}) }
      delete requestOverrides.columnFilters
      delete requestOverrides.silent
      const effectiveSortField = requestOverrides.sort_by ?? sortField
      const effectiveSortOrder = requestOverrides.sort_order ?? (sortOrder === 'descend' ? 'desc' : 'asc')
      const result = await getDevices({
        skip: (currentPage - 1) * pageSize,
        limit: pageSize,
        search: effectiveSearch,
        search_mode: effectiveSearch ? getSearchMode(effectiveSearch) : 'fuzzy',
        status: statusFilter,
        device_type_id: deviceTypeFilter,
        device_role: roleFilter,
        vendor: vendorFilter,
        is_monitored: monitoredFilter === undefined ? undefined : monitoredFilter === 'true',
        datacenter_id: datacenterFilter,
        ...getColumnFilterParams(effectiveColumnFilters),
        sort_by: effectiveSortField,
        sort_order: effectiveSortOrder,
        ...requestOverrides,
      })
      const nextItems = effectiveSortField === 'ip_address'
        ? [...result.items].sort((left, right) => {
          const order = compareIpAddress(left.ip_address, right.ip_address)
          return effectiveSortOrder === 'desc' ? -order : order
        })
        : result.items
      setDevices(nextItems)
      setTotal(result.total)
    } catch (error) {
      console.error('获取设备列表失败:', error)
    } finally {
      if (!silent) {
        setLoading(false)
      }
    }
  }

  const fetchFilterOptions = async () => {
    try {
      const options = await getDeviceFilterOptions()
      setFilterOptions(options)
    } catch (error) {
      console.error('获取筛选选项失败:', error)
    }
  }

  useEffect(() => {
    const resetFromRoute = searchParams.get('reset') === '1'
    const statusFromRoute = searchParams.get('status') || undefined
    if (resetFromRoute) {
      localStorage.removeItem(DEVICE_LIST_STORAGE_KEY)
      setSearchKeyword('')
      setStatusFilter(statusFromRoute)
      setRoleFilter(undefined)
      setVendorFilter(undefined)
      setDatacenterFilter(undefined)
      setDeviceTypeFilter(undefined)
      setColumnFilters({})
      setSortField(undefined)
      setSortOrder(undefined)
      setCurrentPage(1)
      setSelectedRowKeys([])
      fetchDevices({
        skip: 0,
        search: undefined,
        status: statusFromRoute,
        device_type_id: undefined,
        device_role: undefined,
        vendor: undefined,
        datacenter_id: undefined,
        columnFilters: {},
        sort_by: undefined,
        sort_order: undefined,
      })
      setSearchParams(statusFromRoute ? { status: statusFromRoute } : {}, { replace: true })
    } else {
      fetchDevices(statusFromRoute ? { status: statusFromRoute, skip: 0 } : undefined)
    }
    fetchFilterOptions()
  }, [])

  useEffect(() => {
    localStorage.setItem(
      DEVICE_LIST_STORAGE_KEY,
      JSON.stringify({
        version: DEVICE_LIST_STORAGE_VERSION,
        currentPage,
        pageSize,
        searchKeyword,
        statusFilter,
        roleFilter,
        vendorFilter,
        monitoredFilter,
        datacenterFilter,
        deviceTypeFilter,
        columnFilters,
        sortField,
        sortOrder,
        columnWidths,
        visibleColumns,
      })
    )
  }, [currentPage, pageSize, searchKeyword, statusFilter, roleFilter, vendorFilter, monitoredFilter, datacenterFilter, deviceTypeFilter, columnFilters, sortField, sortOrder, columnWidths, visibleColumns])

  useEffect(() => {
    return () => {
      if (searchTimerRef.current) {
        window.clearTimeout(searchTimerRef.current)
      }
    }
  }, [])

  useEffect(() => {
    const handleMouseMove = (event: MouseEvent) => {
      const resizeState = resizeStateRef.current
      if (!resizeState) {
        return
      }

      const nextWidth = Math.max(90, resizeState.startWidth + event.clientX - resizeState.startX)
      setColumnWidths((prev) => ({
        ...prev,
        [resizeState.key]: nextWidth,
      }))
    }

    const handleMouseUp = () => {
      resizeStateRef.current = null
    }

    document.addEventListener('mousemove', handleMouseMove)
    document.addEventListener('mouseup', handleMouseUp)

    return () => {
      document.removeEventListener('mousemove', handleMouseMove)
      document.removeEventListener('mouseup', handleMouseUp)
    }
  }, [])

  const handleSearch = (value: string) => {
    setSearchKeyword(value)
    setCurrentPage(1)
    fetchDevices({
      search: value,
      search_mode: value ? getSearchMode(value) : 'fuzzy',
      skip: 0,
    })
  }

  const handleSearchChange = (value: string) => {
    setSearchKeyword(value)
    setCurrentPage(1)

    if (searchTimerRef.current) {
      window.clearTimeout(searchTimerRef.current)
    }

    searchTimerRef.current = window.setTimeout(() => {
      fetchDevices({
        search: value || undefined,
        search_mode: value ? getSearchMode(value) : 'fuzzy',
        skip: 0,
        silent: true,
      })
    }, 120)
  }

  const handleResizeStart = (key: string) => (event: ReactMouseEvent<HTMLSpanElement>) => {
    event.preventDefault()
    resizeStateRef.current = {
      key,
      startX: event.clientX,
      startWidth: columnWidths[key],
    }
  }

  const handleStatusChange = (value: string | undefined) => {
    setStatusFilter(value)
    setCurrentPage(1)
    fetchDevices({ status: value, skip: 0, silent: true })
  }

  const handleDatacenterChange = (value: number | undefined) => {
    setDatacenterFilter(value)
    setCurrentPage(1)
    fetchDevices({ datacenter_id: value, skip: 0, silent: true })
  }

  const handleRoleChange = (value: string | undefined) => {
    setRoleFilter(value)
    setCurrentPage(1)
    fetchDevices({ device_role: value, skip: 0, silent: true })
  }

  const handleDeviceTypeChange = (value: number | undefined) => {
    setDeviceTypeFilter(value)
    setCurrentPage(1)
    fetchDevices({ device_type_id: value, skip: 0, silent: true })
  }

  const handleVendorChange = (value: string | undefined) => {
    setVendorFilter(value)
    setCurrentPage(1)
    fetchDevices({ vendor: value, skip: 0, silent: true })
  }

  const handleMonitoredChange = (value: string | undefined) => {
    setMonitoredFilter(value)
    setCurrentPage(1)
    fetchDevices({
      is_monitored: value === undefined ? undefined : value === 'true',
      skip: 0,
      silent: true,
    })
  }

  const handleResetFilters = () => {
    if (searchTimerRef.current) {
      window.clearTimeout(searchTimerRef.current)
    }
    setSearchKeyword('')
    setStatusFilter(undefined)
    setRoleFilter(undefined)
    setVendorFilter(undefined)
    setMonitoredFilter(undefined)
    setDatacenterFilter(undefined)
    setDeviceTypeFilter(undefined)
    setColumnFilters({})
    setSortField(undefined)
    setSortOrder(undefined)
    setCurrentPage(1)
    setSelectedRowKeys([])
    setSearchParams({}, { replace: true })
    fetchDevices({
      skip: 0,
      search: undefined,
      status: undefined,
      device_type_id: undefined,
      device_role: undefined,
      vendor: undefined,
      is_monitored: undefined,
      datacenter_id: undefined,
      columnFilters: {},
      sort_by: undefined,
      sort_order: undefined,
    })
  }

  const handleTableChange = (pagination: any, filters: any, sorter: any) => {
    const nextColumnFilters = COLUMN_FILTER_KEYS.reduce<Partial<Record<ColumnFilterKey, string>>>((result, key) => {
      const value = filters?.[key]?.[0]
      if (value !== undefined && value !== null && String(value).trim()) {
        result[key] = String(value).trim()
      }
      return result
    }, {})
    const nextSortField = sorter?.order ? sorter.columnKey as SortField : undefined
    const nextSortOrder = sorter?.order as SortOrder
    setCurrentPage(pagination.current)
    setPageSize(pagination.pageSize)
    setColumnFilters(nextColumnFilters)
    setSortField(nextSortField)
    setSortOrder(nextSortOrder)
    fetchDevices({
      skip: (pagination.current - 1) * pagination.pageSize,
      limit: pagination.pageSize,
      columnFilters: nextColumnFilters,
      sort_by: nextSortField,
      sort_order: nextSortOrder === 'descend' ? 'desc' : 'asc',
    })
  }

  const handleDelete = async (id: number) => {
    try {
      await deleteDevice(id)
      message.success('删除成功')
      setSelectedRowKeys((prev) => prev.filter((key) => key !== id))
      fetchDevices()
    } catch (error) {
      console.error('删除失败:', error)
    }
  }

  const handleBatchDelete = async () => {
    if (selectedRowKeys.length === 0) {
      message.warning('请先选择需要删除的设备')
      return
    }

    setBatchDeleting(true)
    try {
      const result = await batchDeleteDevices(selectedRowKeys.map((key) => Number(key)))
      message.success(`批量删除完成：成功删除 ${result.deleted} 台设备`)
      if (result.missing_ids.length > 0) {
        message.warning(`其中 ${result.missing_ids.length} 台设备不存在或已被删除`)
      }
      setSelectedRowKeys([])
      fetchDevices()
      fetchFilterOptions()
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '批量删除失败')
    } finally {
      setBatchDeleting(false)
    }
  }

  const handleOpenBatchEdit = () => {
    if (selectedRowKeys.length === 0) {
      message.warning('请先选择需要修改的设备')
      return
    }
    batchEditForm.resetFields()
    setBatchEditVisible(true)
  }

  const handleBatchFieldChange = () => {
    batchEditForm.setFieldsValue({
      value: undefined,
      value_id: undefined,
    })
  }

  const handleBatchUpdate = async () => {
    try {
      const values = await batchEditForm.validateFields()
      setBatchUpdating(true)

      const payload: { device_ids: number[]; field: string; value?: string; value_id?: number } = {
        device_ids: selectedRowKeys.map((key) => Number(key)),
        field: values.field,
      }

      if (values.field === 'datacenter_id' || values.field === 'device_type') {
        payload.value_id = values.value_id
      } else {
        payload.value = values.value
      }

      const result = await batchUpdateDevices(payload)
      message.success(`批量修改完成：成功更新 ${result.updated} 台设备`)
      if (result.missing_ids.length > 0) {
        message.warning(`其中 ${result.missing_ids.length} 台设备不存在或已被删除`)
      }
      setBatchEditVisible(false)
      setSelectedRowKeys([])
      fetchDevices()
      fetchFilterOptions()
    } catch (error: any) {
      if (error?.errorFields) {
        return
      }
      message.error(error?.response?.data?.detail || '批量修改失败')
    } finally {
      setBatchUpdating(false)
    }
  }

  const handleExport = async () => {
    try {
      const blob = await exportDevices()
      const url = window.URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `devices_${new Date().toISOString().split('T')[0]}.csv`
      link.click()
      window.URL.revokeObjectURL(url)
      message.success('导出成功')
    } catch (error) {
      message.error('导出失败')
    }
  }

  const handleExportTemplate = async () => {
    try {
      const blob = await exportDeviceTemplate()
      const url = window.URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = 'device_import_template.csv'
      link.click()
      window.URL.revokeObjectURL(url)
      message.success('模板下载成功')
    } catch (error) {
      message.error('模板下载失败')
    }
  }

  const handleImportClick = () => {
    fileInputRef.current?.click()
  }

  const handleImportFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (!file) {
      return
    }

    const normalizedName = file.name.toLowerCase()
    if (!normalizedName.endsWith('.csv')) {
      message.error('只支持导入 CSV 文件')
      event.target.value = ''
      return
    }

    setImporting(true)
    const hideImporting = message.loading(`正在导入 ${file.name}，请稍等...`, 0)
    try {
      const result = await importDevices(file)
      hideImporting()
      message.success(`导入完成：成功 ${result.imported} 条，失败 ${result.failed} 条`)
      if (result.errors.length > 0) {
        message.warning(result.errors.slice(0, 3).join('；'))
      }
      fetchDevices()
      fetchFilterOptions()
    } catch (error: any) {
      hideImporting()
      message.error(error?.response?.data?.detail || '导入失败')
    } finally {
      setImporting(false)
      event.target.value = ''
    }
  }

  const getStatusBadge = (status: string) => {
    const statusMap: Record<string, { text: string; background: string; color: string; borderColor: string }> = {
      active: { text: '上线', background: '#52c41a', color: '#fff', borderColor: '#52c41a' },
      inactive: { text: '离线', background: '#8c8c8c', color: '#fff', borderColor: '#8c8c8c' },
      in_stock: { text: '库存', background: '#d9d9d9', color: '#434343', borderColor: '#d9d9d9' },
      deployed: { text: '上架', background: '#1677ff', color: '#fff', borderColor: '#1677ff' },
      online: { text: '上线', background: '#52c41a', color: '#fff', borderColor: '#52c41a' },
      offline: { text: '离线', background: '#8c8c8c', color: '#fff', borderColor: '#8c8c8c' },
    }
    const config = statusMap[status] || statusMap.in_stock
    return (
        <span
          style={{
          display: 'inline-flex',
          alignItems: 'center',
          justifyContent: 'center',
          minWidth: 42,
          padding: '2px 8px',
          fontSize: 12,
          lineHeight: 1.4,
          fontWeight: 700,
          borderRadius: 4,
          background: config.background,
          color: config.color,
          border: `1px solid ${config.borderColor}`,
          whiteSpace: 'nowrap',
        }}
      >
        {config.text}
      </span>
    )
  }

  const renderFilterChips = <T extends string | number>(params: {
    label: string
    value: T | undefined
    onChange: (value: T | undefined) => void
    options: Array<{ value: T; label: string }>
  }) => {
    const { label, value, onChange, options } = params

    return (
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
        <div style={{ minWidth: 84, fontWeight: 700, color: '#333', lineHeight: '30px' }}>
          {label}
        </div>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 0, flex: 1 }}>
          <Button
            size="small"
            type={value === undefined ? 'primary' : 'default'}
            onClick={() => onChange(undefined)}
            style={{ borderRadius: 0, minWidth: 40, marginRight: -1, marginBottom: -1 }}
          >
            全部
          </Button>
          {options.map((option) => (
            <Button
              key={String(option.value)}
              size="small"
              type={value === option.value ? 'primary' : 'default'}
              onClick={() => onChange(value === option.value ? undefined : option.value)}
              style={{ borderRadius: 0, marginRight: -1, marginBottom: -1 }}
            >
              {option.label}
            </Button>
          ))}
        </div>
      </div>
    )
  }

  const getColumnSearchProps = (key: ColumnFilterKey, placeholder: string) => ({
    sorter: true,
    sortOrder: sortField === key ? sortOrder : null,
    filteredValue: columnFilters[key] ? [columnFilters[key] as string] : null,
    filterIcon: (filtered: boolean) => (
      <SearchOutlined style={{ color: filtered ? '#1677ff' : undefined }} />
    ),
    filterDropdown: ({ setSelectedKeys, selectedKeys, confirm, clearFilters, close }: any) => (
      <div style={{ padding: 10, width: 240 }} onKeyDown={(event) => event.stopPropagation()}>
        <Input
          autoFocus
          allowClear
          placeholder={placeholder}
          value={selectedKeys[0] || ''}
          onChange={(event) => setSelectedKeys(event.target.value ? [event.target.value] : [])}
          onPressEnter={() => confirm()}
          style={{ marginBottom: 8 }}
        />
        <Space>
          <Button type="primary" size="small" icon={<SearchOutlined />} onClick={() => confirm()}>
            筛选
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
          <Button size="small" type="link" onClick={() => close()}>
            关闭
          </Button>
        </Space>
      </div>
    ),
  })

  const allColumns: any[] = [
    {
      title: '设备名称',
      dataIndex: 'name',
      key: 'name',
      width: columnWidths.name,
      ellipsis: true,
      ...getColumnSearchProps('name', '输入设备名称'),
      render: (text: string) => (
        <span title={text} style={{ ...TABLE_CELL_TEXT_STYLE, fontWeight: 500 }}>
          {text}
        </span>
      ),
    },
    {
      title: '运行状态',
      dataIndex: 'status',
      key: 'status',
      width: columnWidths.status,
      ...getColumnSearchProps('status', '输入上线、离线、库存或上架'),
      render: (status: string) => getStatusBadge(status),
    },
    {
      title: '管理地址',
      dataIndex: 'ip_address',
      key: 'ip_address',
      width: columnWidths.ip_address,
      ellipsis: true,
      className: 'network-device-ip-column',
      ...getColumnSearchProps('ip_address', '输入管理地址'),
      render: (value: string) => (
        <span title={value || '-'} style={TABLE_CELL_TEXT_STYLE}>
          {value || '-'}
        </span>
      ),
    },
    {
      title: '机房',
      dataIndex: ['datacenter', 'name'],
      key: 'datacenter',
      width: columnWidths.datacenter,
      ellipsis: true,
      ...getColumnSearchProps('datacenter', '输入机房名称、编号或位置'),
      render: (_: string, record: Device) => {
        if (!record.datacenter) {
          return <span style={TABLE_CELL_TEXT_STYLE}>-</span>
        }
        const text = record.datacenter.code ? `${record.datacenter.name} (${record.datacenter.code})` : record.datacenter.name
        return (
          <span title={text} style={TABLE_CELL_TEXT_STYLE}>
            {text}
          </span>
        )
      },
    },
    {
      title: '是否监控',
      dataIndex: 'is_monitored',
      key: 'is_monitored',
      width: columnWidths.is_monitored,
      ...getColumnSearchProps('is_monitored', '输入监控中或未监控'),
      render: (value: boolean) => (
        <span
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            minWidth: 42,
            padding: '2px 8px',
            fontSize: 12,
            lineHeight: 1.4,
            fontWeight: 700,
            borderRadius: 4,
            background: value ? '#f6ffed' : '#fff7e6',
            color: value ? '#389e0d' : '#d46b08',
            border: `1px solid ${value ? '#b7eb8f' : '#ffd591'}`,
            whiteSpace: 'nowrap',
          }}
        >
          {value ? '监控中' : '未监控'}
        </span>
      ),
    },
    {
      title: '设备类型',
      dataIndex: 'device_type',
      key: 'device_type',
      width: columnWidths.device_type,
      ellipsis: true,
      ...getColumnSearchProps('device_type', '输入设备类型'),
      render: (type: string, record: Device) => {
        if (record.device_type) {
          const typeMap: Record<string, string> = {
            Firewall: '防火墙',
            Switch: '交换机',
            Router: '路由器',
            Console: '控制台',
          }
          const text = typeMap[record.device_type] || record.device_type
          return (
            <span title={text} style={TABLE_CELL_TEXT_STYLE}>
              {text}
            </span>
          )
        }
        return <span style={TABLE_CELL_TEXT_STYLE}>{type || '-'}</span>
      },
    },
    {
      title: '设备角色',
      dataIndex: 'device_role',
      key: 'device_role',
      width: columnWidths.device_role,
      ellipsis: true,
      render: (text: string) => (
        <span title={text || '-'} style={TABLE_CELL_TEXT_STYLE}>
          {text || '-'}
        </span>
      ),
    },
    {
      title: '厂商',
      dataIndex: 'vendor',
      key: 'vendor',
      width: columnWidths.vendor,
      ellipsis: true,
      render: (text: string) => (
        <span title={text || '-'} style={TABLE_CELL_TEXT_STYLE}>
          {text || '-'}
        </span>
      ),
    },
    {
      title: '型号',
      dataIndex: 'model',
      key: 'model',
      width: columnWidths.model,
      ellipsis: true,
      ...getColumnSearchProps('model', '输入型号'),
      render: (text: string) => (
        <span title={text || '-'} style={TABLE_CELL_TEXT_STYLE}>
          {text || '-'}
        </span>
      ),
    },
    {
      title: '序列号',
      dataIndex: 'serial_number',
      key: 'serial_number',
      width: columnWidths.serial_number,
      ellipsis: true,
      ...getColumnSearchProps('serial_number', '输入序列号'),
      render: (text: string) => (
        <span title={text || '-'} style={TABLE_CELL_TEXT_STYLE}>
          {text || '-'}
        </span>
      ),
    },
    {
      title: '操作',
      key: 'action',
      width: columnWidths.action,
      render: (_: any, record: Device) => (
        <Space>
          <Tooltip title="查看详情">
            <Button type="text" icon={<EyeOutlined />} onClick={() => navigate(`/devices/${record.id}`)} />
          </Tooltip>
          {canModify ? (
            <>
              <Tooltip title="编辑">
                <Button type="text" icon={<EditOutlined />} onClick={() => navigate(`/devices/edit/${record.id}`)} />
              </Tooltip>
              <Popconfirm
                title="确认删除"
                description={`确定要删除设备 "${record.name}" 吗？`}
                onConfirm={() => handleDelete(record.id)}
                okText="删除"
                cancelText="取消"
                okButtonProps={{ danger: true }}
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
  ]

  const columnToggleOptions = [
    { key: 'name', label: '设备名称' },
    { key: 'ip_address', label: '管理地址' },
    { key: 'status', label: '运行状态' },
    { key: 'is_monitored', label: '是否监控' },
    { key: 'datacenter', label: '机房' },
    { key: 'device_role', label: '设备角色' },
    { key: 'model', label: '型号' },
    { key: 'device_type', label: '设备类型' },
    { key: 'vendor', label: '厂商' },
    { key: 'serial_number', label: '序列号' },
    { key: 'action', label: '操作' },
  ]
  const columns = allColumns
    .filter((column) => visibleColumns.includes(column.key))
    .sort((left, right) => COLUMN_ORDER.indexOf(left.key) - COLUMN_ORDER.indexOf(right.key))
    .map((column, index) => ({
      ...column,
      onHeaderCell: () => ({
        onResizeStart: index === 0 ? undefined : handleResizeStart(column.key),
        resizeHandleSide: 'left',
      }),
    }))
  const batchField = Form.useWatch('field', batchEditForm)

  return (
    <Card>
      <style>{`
        .network-device-table .ant-table {
          font-size: 12px;
        }
        .network-device-table .ant-table-thead > tr > th {
          background: #f3f3f3;
          color: #444;
          font-weight: 700;
          font-size: 12px;
          padding: 10px 8px;
          white-space: nowrap;
        }
        .network-device-table .ant-table-tbody > tr > td {
          padding: 9px 8px;
          font-size: 12px;
          vertical-align: middle;
          white-space: nowrap;
        }
        .network-device-table .ant-table-thead > tr > th.network-device-ip-column,
        .network-device-table .ant-table-tbody > tr > td.network-device-ip-column {
          padding-left: 6px;
          padding-right: 6px;
        }
        .network-device-table .ant-table-tbody > tr:hover > td {
          background: #fafcff;
        }
        .network-device-toolbar .ant-btn,
        .network-device-toolbar .ant-input,
        .network-device-toolbar .ant-input-search-button {
          font-size: 12px;
        }
      `}</style>
      <div
        style={{
          marginBottom: 16,
          padding: 12,
          background: colorBgContainer,
          border: `1px solid ${colorBorder}`,
          borderRadius: 4,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
          <div style={{ fontWeight: 'bold', fontSize: 14, color: colorText }}>▼ 过滤器</div>
          <Tooltip title="重置筛选">
            <Button size="small" icon={<ReloadOutlined />} onClick={handleResetFilters}>
              重置筛选
            </Button>
          </Tooltip>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {renderFilterChips({
            label: '运行状态',
            value: statusFilter,
            onChange: handleStatusChange,
            options: [
              { value: 'active', label: '上线' },
              { value: 'inactive', label: '离线' },
              { value: 'in_stock', label: '库存' },
              { value: 'deployed', label: '上架' },
            ],
          })}
          {renderFilterChips({
            label: '机房',
            value: datacenterFilter,
            onChange: handleDatacenterChange,
            options: filterOptions.datacenters.map((dc) => ({
              value: dc.id,
              label: dc.code ? `${dc.name} (${dc.code})` : dc.name,
            })),
          })}
          {renderFilterChips({
            label: '设备类型',
            value: deviceTypeFilter,
            onChange: handleDeviceTypeChange,
            options: filterOptions.device_types.map((dt) => ({
              value: dt.id,
              label: dt.display_name || dt.name,
            })),
          })}
          {renderFilterChips({
            label: '设备角色',
            value: roleFilter,
            onChange: handleRoleChange,
            options: filterOptions.device_roles.map((deviceRole) => ({
              value: deviceRole,
              label: deviceRole,
            })),
          })}
          {renderFilterChips({
            label: '厂商',
            value: vendorFilter,
            onChange: handleVendorChange,
            options: filterOptions.vendors.map((vendor) => ({
              value: vendor,
              label: vendor,
            })),
          })}
          {renderFilterChips({
            label: '监控状态',
            value: monitoredFilter,
            onChange: handleMonitoredChange,
            options: [
              { value: 'true', label: '已监控' },
              { value: 'false', label: '未监控' },
            ],
          })}
        </div>
        <div style={{ marginTop: 12 }}>
          <Text type="secondary">
            导入/导出字段：设备名称、运行状态、IP地址、设备角色、设备类型、厂商、型号、序列号、机房名称、机房编号、是否加入监控、SSH端口、SSH用户名、SSH密码、SSH私钥
          </Text>
        </div>
      </div>

      <div className="network-device-toolbar" style={{ marginBottom: 16, display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <Space>
          <Search
            placeholder="搜索任意设备字段，支持正则"
            value={searchKeyword}
            allowClear
            enterButton={<SearchOutlined />}
            onSearch={handleSearch}
            onChange={(event) => handleSearchChange(event.target.value)}
            style={{ width: 320 }}
          />
        </Space>
        <Space>
          {canModify && selectedRowKeys.length > 0 ? (
            <Button icon={<EditOutlined />} onClick={handleOpenBatchEdit}>
              批量修改 ({selectedRowKeys.length})
            </Button>
          ) : null}
          {canModify && selectedRowKeys.length > 0 ? (
            <Popconfirm
              title="确认批量删除"
              description={`确定要删除选中的 ${selectedRowKeys.length} 台设备吗？`}
              onConfirm={handleBatchDelete}
              okText="删除"
              cancelText="取消"
              okButtonProps={{ danger: true, loading: batchDeleting }}
            >
              <Button danger icon={<DeleteOutlined />} loading={batchDeleting}>
                批量删除 ({selectedRowKeys.length})
              </Button>
            </Popconfirm>
          ) : null}
          {canModify ? (
            <>
              <input
                ref={fileInputRef}
                type="file"
                accept=".csv"
                style={{ display: 'none' }}
                onChange={handleImportFile}
              />
              <Tooltip title="导入">
                <Button icon={<ImportOutlined />} onClick={handleImportClick} loading={importing} disabled={importing}>
                  {importing ? '导入中...' : '导入'}
                </Button>
              </Tooltip>
            </>
          ) : null}
          <Tooltip title="下载导入模板">
            <Button icon={<DownloadOutlined />} onClick={handleExportTemplate}>
              导出模板
            </Button>
          </Tooltip>
          <Tooltip title="导出">
            <Button icon={<ExportOutlined />} onClick={handleExport}>
              导出
            </Button>
          </Tooltip>
          <Dropdown
            trigger={['click']}
            dropdownRender={() => (
              <div
                style={{
                  background: colorBgElevated,
                  border: `1px solid ${colorBorder}`,
                  borderRadius: 8,
                  boxShadow: '0 6px 16px rgba(0,0,0,0.12)',
                  padding: 12,
                  width: 220,
                }}
              >
                <div style={{ fontWeight: 600, marginBottom: 8, color: colorTextSecondary }}>显示/隐藏列</div>
                <Checkbox.Group
                  value={visibleColumns}
                  onChange={(values) => setVisibleColumns(normalizeVisibleColumns(values as string[]))}
                  style={{ display: 'flex', flexDirection: 'column', gap: 8 }}
                >
                  {columnToggleOptions.map((option) => (
                    <Checkbox key={option.key} value={option.key}>
                      {option.label}
                    </Checkbox>
                  ))}
                </Checkbox.Group>
              </div>
            )}
          >
            <Tooltip title="显示或隐藏列">
              <Button icon={<SettingOutlined />}>
                显示/隐藏列
              </Button>
            </Tooltip>
          </Dropdown>
          {canModify ? (
            <Tooltip title="添加设备">
              <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/devices/add')}>
                添加设备
              </Button>
            </Tooltip>
          ) : null}
        </Space>
      </div>

      <div className="network-device-table">
      <Table
        rowSelection={canModify ? {
          selectedRowKeys,
          onChange: (keys) => setSelectedRowKeys(keys),
        } : undefined}
        components={{
          header: {
            cell: ResizableHeaderCell,
          },
        } as any}
        columns={columns}
        dataSource={devices}
        rowKey="id"
        loading={loading}
        size="small"
        bordered
        tableLayout="fixed"
        pagination={{
          total,
          current: currentPage,
          pageSize,
          showSizeChanger: true,
          showTotal: (all) => `共 ${all} 条`,
          pageSizeOptions: ['10', '20', '50', '100'],
        }}
        onChange={handleTableChange}
        scroll={{ x: 'max-content' }}
      />
      </div>

      <Modal
        title="批量修改设备"
        open={batchEditVisible}
        onCancel={() => setBatchEditVisible(false)}
        onOk={handleBatchUpdate}
        okText="确认修改"
        cancelText="取消"
        confirmLoading={batchUpdating}
        destroyOnClose
      >
        <Form form={batchEditForm} layout="vertical">
          <Form.Item
            name="field"
            label="修改字段"
            rules={[{ required: true, message: '请选择需要修改的字段' }]}
          >
            <Select placeholder="选择字段" onChange={handleBatchFieldChange}>
              <Option value="status">运行状态</Option>
              <Option value="is_monitored">是否监控</Option>
              <Option value="datacenter_id">所属机房</Option>
              <Option value="device_type">设备类型</Option>
              <Option value="device_role">设备角色</Option>
              <Option value="vendor">厂商</Option>
              <Option value="model">型号</Option>
              <Option value="serial_number">序列号</Option>
            </Select>
          </Form.Item>

          {batchField === 'status' ? (
            <Form.Item name="value" label="新值" rules={[{ required: true, message: '请选择运行状态' }]}>
              <Select placeholder="选择运行状态">
                <Option value="active">上线</Option>
                <Option value="inactive">离线</Option>
                <Option value="in_stock">库存</Option>
                <Option value="deployed">上架</Option>
              </Select>
            </Form.Item>
          ) : null}

          {batchField === 'is_monitored' ? (
            <Form.Item name="value" label="新值" rules={[{ required: true, message: '请选择是否监控' }]}>
              <Select placeholder="选择是否监控">
                <Option value="true">监控中</Option>
                <Option value="false">未监控</Option>
              </Select>
            </Form.Item>
          ) : null}

          {batchField === 'datacenter_id' ? (
            <Form.Item name="value_id" label="新值" rules={[{ required: true, message: '请选择机房' }]}>
              <Select placeholder="选择机房" showSearch optionFilterProp="label">
                {filterOptions.datacenters.map((dc) => (
                  <Option key={dc.id} value={dc.id} label={dc.code ? `${dc.name} (${dc.code})` : dc.name}>
                    {dc.code ? `${dc.name} (${dc.code})` : dc.name}
                  </Option>
                ))}
              </Select>
            </Form.Item>
          ) : null}

          {batchField === 'device_type' ? (
            <Form.Item name="value_id" label="新值" rules={[{ required: true, message: '请选择设备类型' }]}>
              <Select placeholder="选择设备类型">
                {filterOptions.device_types.map((dt) => (
                  <Option key={dt.id} value={dt.id}>
                    {dt.display_name || dt.name}
                  </Option>
                ))}
              </Select>
            </Form.Item>
          ) : null}

          {batchField === 'device_role' ? (
            <Form.Item name="value" label="新值" rules={[{ required: true, message: '请选择设备角色' }]}>
              <Select placeholder="选择设备角色" showSearch>
                {filterOptions.device_roles.map((role) => (
                  <Option key={role} value={role}>
                    {role}
                  </Option>
                ))}
              </Select>
            </Form.Item>
          ) : null}

          {batchField === 'vendor' ? (
            <Form.Item name="value" label="新值" rules={[{ required: true, message: '请选择厂商' }]}>
              <Select placeholder="选择厂商" showSearch>
                {filterOptions.vendors.map((vendor) => (
                  <Option key={vendor} value={vendor}>
                    {vendor}
                  </Option>
                ))}
              </Select>
            </Form.Item>
          ) : null}

          {batchField === 'model' ? (
            <Form.Item name="value" label="新值" rules={[{ required: true, message: '请输入型号' }]}>
              <Input placeholder="输入型号" />
            </Form.Item>
          ) : null}

          {batchField === 'serial_number' ? (
            <Form.Item name="value" label="新值" rules={[{ required: true, message: '请输入序列号' }]}>
              <Input placeholder="输入序列号" />
            </Form.Item>
          ) : null}
        </Form>
      </Modal>
    </Card>
  )
}

export default DeviceList
