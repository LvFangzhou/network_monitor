import { useEffect, useMemo, useState } from 'react'
import type { DragEvent, MouseEvent as ReactMouseEvent } from 'react'
import { Button, Card, Checkbox, Dropdown, Input, Select, Space, Table, Tag, Tooltip, Typography, message } from 'antd'
import { ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import {
  getControllerOptions,
  getControllerOpticals,
  type ControllerOption,
} from '../../api/controller'

const { Text } = Typography

const COLUMN_ORDER_STORAGE_KEY = 'module-info-visible-columns-v2'
const DEFAULT_VISIBLE_COLUMN_KEYS = [
  'datacenterName',
  'source',
  'ifOperStatus',
  'adminStatus',
  'vendorName',
  'serialNumber',
  'transceiveType',
  'transceiverSpeed',
  'curRxPower',
  'curTxPower',
  'curTemperature',
  'curVoltage',
  'mfgDate',
  'time',
]

const SOURCE_LABELS: Record<string, string> = {
  controller_api: '控制器API',
  snmp: '设备SNMP',
  netconf: 'NETCONF',
  gnmi: 'gNMI Telemetry',
}

const formatNumber = (value?: number | string | null) => {
  if (value === undefined || value === null || value === '') return null
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : null
}

const normalizePower = (value?: number | string | null) => {
  const numeric = formatNumber(value)
  if (numeric === null) return null
  return numeric * 0.01
}

const normalizeTemperature = (value?: number | string | null) => {
  const numeric = formatNumber(value)
  if (numeric === null) return null
  return Math.abs(numeric) > 1000 ? numeric / 1000 : numeric
}

const normalizeVoltage = (value?: number | string | null) => {
  const numeric = formatNumber(value)
  if (numeric === null) return null
  return numeric * 0.01
}

const isPowerAbnormal = (value: number | null) => value !== null && (value < -10 || value > 3)
const isTemperatureAbnormal = (value: number | null) => value !== null && (value < 0 || value > 70)
const isVoltageAbnormal = (value: number | null) => value !== null && (value < 3 || value > 3.6)

const MetricText = ({ value, unit, danger }: { value: number | null; unit: string; danger: boolean }) => {
  if (value === null) return <Text type="secondary">-</Text>
  return (
    <Tooltip title={danger ? '超过常用安全范围，请结合模块规格确认阈值' : undefined}>
      <Text type={danger ? 'danger' : undefined} strong={danger}>
        {value.toFixed(unit === '℃' ? 1 : 2)} {unit}
      </Text>
    </Tooltip>
  )
}

const ipToParts = (ip?: string) => {
  const parts = String(ip || '').split('.').map((item) => Number(item))
  if (parts.length !== 4 || parts.some((item) => !Number.isInteger(item) || item < 0 || item > 255)) {
    return [999, 999, 999, 999]
  }
  return parts
}

const compareIp = (left?: string, right?: string) => {
  const a = ipToParts(left)
  const b = ipToParts(right)
  for (let index = 0; index < 4; index += 1) {
    if (a[index] !== b[index]) return a[index] - b[index]
  }
  return String(left || '').localeCompare(String(right || ''), undefined, { numeric: true, sensitivity: 'base' })
}

const compareNatural = (left?: string | number | null, right?: string | number | null) =>
  String(left ?? '').localeCompare(String(right ?? ''), undefined, { numeric: true, sensitivity: 'base' })

const getInterfaceName = (record: any) => record.ifDesc || record.interfaceName || record.ifName || ''
const getDeviceIp = (record: any) => record.deviceIp || record.ip || ''
const getSourceLabel = (record: any) => record.source || SOURCE_LABELS[record.sourceType] || record.sourceType || '控制器API'

const ModuleInfoQuery = () => {
  const [controllers, setControllers] = useState<ControllerOption[]>([])
  const [controllerId, setControllerId] = useState<string>()
  const [loading, setLoading] = useState(false)
  const [items, setItems] = useState<any[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(100)
  const [search, setSearch] = useState('')
  const [deviceIp, setDeviceIp] = useState('')
  const [interfaceName, setInterfaceName] = useState('')
  const [vendorName, setVendorName] = useState('')
  const [draggingColumnKey, setDraggingColumnKey] = useState<string | null>(null)
  const [dragOverColumnKey, setDragOverColumnKey] = useState<string | null>(null)
  const [columnWidths, setColumnWidths] = useState<Record<string, number>>({})
  const [visibleColumnKeys, setVisibleColumnKeys] = useState<string[]>(() => {
    try {
      const raw = window.localStorage.getItem(COLUMN_ORDER_STORAGE_KEY)
      const saved = raw ? JSON.parse(raw) : null
      return Array.isArray(saved) && saved.length > 0 ? saved.map(String) : DEFAULT_VISIBLE_COLUMN_KEYS
    } catch {
      return DEFAULT_VISIBLE_COLUMN_KEYS
    }
  })

  const controllerOptions = useMemo(
    () => controllers.map((item) => ({ value: item.id, label: `${item.name}（${item.base_url}）` })),
    [controllers],
  )

  const vendorOptions = useMemo(() => {
    const values = Array.from(new Set(items.map((item) => String(item.vendorName || '').trim()).filter(Boolean)))
      .sort((left, right) => left.localeCompare(right, undefined, { numeric: true, sensitivity: 'base' }))
    return [{ value: '', label: '全部厂商' }, ...values.map((value) => ({ value, label: value }))]
  }, [items])

  const loadControllers = async () => {
    try {
      const result = await getControllerOptions()
      setControllers(result.items || [])
      setControllerId((current) => current || result.items?.[0]?.id)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '获取控制器列表失败')
    }
  }

  const loadData = async (nextPage = page, nextPageSize = pageSize) => {
    if (!controllerId) return
    setLoading(true)
    try {
      const result = await getControllerOpticals({
        controller_id: controllerId,
        page: nextPage,
        page_size: nextPageSize,
        search: search.trim() || undefined,
        device_ip: deviceIp.trim() || undefined,
        interface_name: interfaceName.trim() || undefined,
        vendor_name: vendorName || undefined,
        hours: 3,
      })
      setItems(result.items || [])
      setTotal(result.total || 0)
      setPage(nextPage)
      setPageSize(nextPageSize)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '查询模块信息失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadControllers()
  }, [])

  useEffect(() => {
    if (controllerId) loadData(1, pageSize)
  }, [controllerId])

  useEffect(() => {
    if (!controllerId) return undefined
    const timer = window.setTimeout(() => {
      loadData(1, pageSize)
    }, 350)
    return () => window.clearTimeout(timer)
  }, [search, deviceIp, interfaceName, vendorName])

  const updateVisibleColumnKeys = (updater: string[] | ((current: string[]) => string[])) => {
    setVisibleColumnKeys((current) => {
      const next = typeof updater === 'function' ? updater(current) : updater
      window.localStorage.setItem(COLUMN_ORDER_STORAGE_KEY, JSON.stringify(next))
      return next
    })
  }

  const toggleColumnVisible = (key: string, checked: boolean) => {
    updateVisibleColumnKeys((current) => {
      if (checked) return current.includes(key) ? current : [...current, key]
      return current.filter((item) => item !== key)
    })
  }

  const moveVisibleColumn = (sourceKey: string, targetKey: string) => {
    if (sourceKey === targetKey) return
    updateVisibleColumnKeys((current) => {
      const sourceIndex = current.indexOf(sourceKey)
      const targetIndex = current.indexOf(targetKey)
      if (sourceIndex < 0 || targetIndex < 0) return current
      const next = [...current]
      const [source] = next.splice(sourceIndex, 1)
      next.splice(targetIndex, 0, source)
      return next
    })
  }

  const columnOptions = [
    { label: '机房', value: 'datacenterName' },
    { label: '信息来源', value: 'source' },
    { label: '运行状态', value: 'ifOperStatus' },
    { label: '管理状态', value: 'adminStatus' },
    { label: '厂商', value: 'vendorName' },
    { label: '序列号', value: 'serialNumber' },
    { label: '类型', value: 'transceiveType' },
    { label: '速率', value: 'transceiverSpeed' },
    { label: '收光', value: 'curRxPower' },
    { label: '发光', value: 'curTxPower' },
    { label: '温度', value: 'curTemperature' },
    { label: '电压', value: 'curVoltage' },
    { label: '生产日期', value: 'mfgDate' },
    { label: '采集时间', value: 'time' },
  ]

  const columnLabelMap = useMemo(() => new Map(columnOptions.map((item) => [item.value, item.label])), [])
  const orderedColumnOptions = useMemo(() => {
    const visibleSet = new Set(visibleColumnKeys)
    const visibleItems = visibleColumnKeys
      .map((key) => columnOptions.find((item) => item.value === key))
      .filter(Boolean) as typeof columnOptions
    const hiddenItems = columnOptions.filter((item) => !visibleSet.has(item.value))
    return [...visibleItems, ...hiddenItems]
  }, [visibleColumnKeys])

  const resizeColumn = (key: string, event: ReactMouseEvent) => {
    event.preventDefault()
    event.stopPropagation()
    const startX = event.clientX
    const startWidth = columnWidths[key] || Number(allColumns.find((column) => column.key === key)?.width || 140)
    const onMouseMove = (moveEvent: MouseEvent) => {
      const nextWidth = Math.max(90, startWidth + moveEvent.clientX - startX)
      setColumnWidths((current) => ({ ...current, [key]: nextWidth }))
    }
    const onMouseUp = () => {
      window.removeEventListener('mousemove', onMouseMove)
      window.removeEventListener('mouseup', onMouseUp)
    }
    window.addEventListener('mousemove', onMouseMove)
    window.addEventListener('mouseup', onMouseUp)
  }

  const allColumns: any[] = [
    {
      title: '设备',
      key: 'device',
      fixed: 'left',
      width: 260,
      sorter: (a: any, b: any) => {
        const ipCompare = compareIp(getDeviceIp(a), getDeviceIp(b))
        return ipCompare || compareNatural(a.deviceName, b.deviceName)
      },
      render: (_: any, record: any) => (
        <Space direction="vertical" size={2}>
          <Text strong>{record.deviceName || record.name || '-'}</Text>
          <Text type="secondary">{getDeviceIp(record) || '-'}</Text>
        </Space>
      ),
    },
    {
      title: '接口',
      key: 'interface',
      fixed: 'left',
      width: 150,
      sorter: (a: any, b: any) => compareNatural(getInterfaceName(a), getInterfaceName(b)),
      defaultSortOrder: 'ascend',
      render: (_: any, record: any) => getInterfaceName(record) || '-',
    },
    { title: '机房', key: 'datacenterName', dataIndex: 'datacenterName', width: 170, sorter: (a: any, b: any) => compareNatural(a.datacenterName, b.datacenterName), render: (value: string) => value || '-' },
    { title: '信息来源', key: 'source', width: 130, sorter: (a: any, b: any) => compareNatural(getSourceLabel(a), getSourceLabel(b)), render: (_: any, record: any) => <Tag color="blue">{getSourceLabel(record)}</Tag> },
    { title: '运行状态', key: 'ifOperStatus', dataIndex: 'ifOperStatus', width: 110, sorter: (a: any, b: any) => Number(a.ifOperStatus || 0) - Number(b.ifOperStatus || 0), render: (value: any) => Number(value) === 1 ? <Tag color="green">UP</Tag> : <Tag>DOWN</Tag> },
    { title: '管理状态', key: 'adminStatus', dataIndex: 'adminStatus', width: 110, sorter: (a: any, b: any) => Number(a.adminStatus || 0) - Number(b.adminStatus || 0), render: (value: any) => Number(value) === 1 ? <Tag color="green">UP</Tag> : <Tag>DOWN</Tag> },
    { title: '厂商', key: 'vendorName', dataIndex: 'vendorName', width: 150, ellipsis: true, sorter: (a: any, b: any) => compareNatural(a.vendorName, b.vendorName), render: (value: string) => value || '-' },
    { title: '序列号', key: 'serialNumber', dataIndex: 'serialNumber', width: 180, ellipsis: true, sorter: (a: any, b: any) => compareNatural(a.serialNumber, b.serialNumber), render: (value: string) => value || '-' },
    { title: '类型', key: 'transceiveType', dataIndex: 'transceiveType', width: 160, ellipsis: true, sorter: (a: any, b: any) => compareNatural(a.transceiveType, b.transceiveType), render: (value: string) => value || '-' },
    { title: '速率', key: 'transceiverSpeed', dataIndex: 'transceiverSpeed', width: 120, sorter: (a: any, b: any) => compareNatural(a.transceiverSpeed, b.transceiverSpeed), render: (value: string) => value || '-' },
    { title: '收光', key: 'curRxPower', dataIndex: 'curRxPower', width: 120, sorter: (a: any, b: any) => (normalizePower(a.curRxPower) ?? -999) - (normalizePower(b.curRxPower) ?? -999), render: (value: any) => <MetricText value={normalizePower(value)} unit="dBm" danger={isPowerAbnormal(normalizePower(value))} /> },
    { title: '发光', key: 'curTxPower', dataIndex: 'curTxPower', width: 120, sorter: (a: any, b: any) => (normalizePower(a.curTxPower) ?? -999) - (normalizePower(b.curTxPower) ?? -999), render: (value: any) => <MetricText value={normalizePower(value)} unit="dBm" danger={isPowerAbnormal(normalizePower(value))} /> },
    { title: '温度', key: 'curTemperature', dataIndex: 'curTemperature', width: 110, sorter: (a: any, b: any) => (normalizeTemperature(a.curTemperature) ?? -999) - (normalizeTemperature(b.curTemperature) ?? -999), render: (value: any) => <MetricText value={normalizeTemperature(value)} unit="℃" danger={isTemperatureAbnormal(normalizeTemperature(value))} /> },
    { title: '电压', key: 'curVoltage', dataIndex: 'curVoltage', width: 110, sorter: (a: any, b: any) => (normalizeVoltage(a.curVoltage) ?? -999) - (normalizeVoltage(b.curVoltage) ?? -999), render: (value: any) => <MetricText value={normalizeVoltage(value)} unit="V" danger={isVoltageAbnormal(normalizeVoltage(value))} /> },
    { title: '生产日期', key: 'mfgDate', dataIndex: 'mfgDate', width: 130, sorter: (a: any, b: any) => compareNatural(a.mfgDate, b.mfgDate), render: (value: string) => value || '-' },
    { title: '采集时间', key: 'time', dataIndex: 'time', width: 170, sorter: (a: any, b: any) => Number(a.time || 0) - Number(b.time || 0), render: (value: any) => value ? new Date(Number(value)).toLocaleString() : '-' },
  ]

  const columnMap = new Map(allColumns.map((column) => [String(column.key), column]))
  const withHeaderTools = (column: any) => {
    const key = String(column?.key || '')
    const draggable = key !== 'device' && key !== 'interface' && visibleColumnKeys.includes(key)
    return {
      ...column,
      width: columnWidths[key] || column.width,
      title: (
        <Tooltip title={draggable ? '按住表头左右拖动；右侧细线可调整列宽' : '右侧细线可调整列宽'}>
          <span style={{ cursor: draggable ? 'grab' : 'default', userSelect: 'none', display: 'inline-flex', alignItems: 'center', gap: 6, position: 'relative' }}>
            {column.title}
            {draggable ? <span style={{ color: '#1677ff', fontSize: 12, opacity: draggingColumnKey === key ? 1 : 0.45 }}>↔</span> : null}
            <span
              onMouseDown={(event) => resizeColumn(key, event)}
              style={{
                position: 'absolute',
                right: -10,
                top: -8,
                width: 10,
                height: 34,
                cursor: 'col-resize',
              }}
            />
          </span>
        </Tooltip>
      ),
      onHeaderCell: () => ({
        draggable,
        onDragStart: (event: DragEvent<HTMLElement>) => {
          if (!draggable) return
          setDraggingColumnKey(key)
          setDragOverColumnKey(null)
          event.dataTransfer.effectAllowed = 'move'
          event.dataTransfer.setData('text/plain', key)
        },
        onDragEnter: (event: DragEvent<HTMLElement>) => {
          if (draggingColumnKey && draggingColumnKey !== key && draggable) {
            event.preventDefault()
            setDragOverColumnKey(key)
          }
        },
        onDragOver: (event: DragEvent<HTMLElement>) => {
          if (draggingColumnKey && draggingColumnKey !== key && draggable) {
            event.preventDefault()
            event.dataTransfer.dropEffect = 'move'
            setDragOverColumnKey(key)
          }
        },
        onDrop: (event: DragEvent<HTMLElement>) => {
          event.preventDefault()
          const sourceKey = draggingColumnKey || event.dataTransfer.getData('text/plain')
          if (sourceKey && draggable) moveVisibleColumn(sourceKey, key)
          setDraggingColumnKey(null)
          setDragOverColumnKey(null)
        },
        onDragEnd: () => {
          setDraggingColumnKey(null)
          setDragOverColumnKey(null)
        },
        style: {
          cursor: draggable ? 'grab' : undefined,
          background: draggingColumnKey === key ? '#d6e4ff' : dragOverColumnKey === key ? '#e6f4ff' : undefined,
          outline: dragOverColumnKey === key ? '2px dashed #1677ff' : undefined,
          outlineOffset: '-4px',
          boxShadow: dragOverColumnKey === key ? 'inset 4px 0 0 #1677ff, 0 0 0 999px rgba(22,119,255,0.04) inset' : undefined,
          transition: 'background 0.18s ease, box-shadow 0.18s ease, outline-color 0.18s ease',
        },
      }),
    }
  }

  const visibleColumns = [
    columnMap.get('device'),
    columnMap.get('interface'),
    ...visibleColumnKeys.map((key) => columnMap.get(key)).filter(Boolean),
  ].filter(Boolean).map(withHeaderTools)

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card bodyStyle={{ padding: 16 }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'center' }}>
          <Select
            style={{ minWidth: 300 }}
            placeholder="选择控制器"
            value={controllerId}
            options={controllerOptions}
            onChange={setControllerId}
          />
          <Input
            allowClear
            prefix={<SearchOutlined />}
            placeholder="模糊搜索：设备/IP/接口/速率/序列号"
            style={{ flex: '1 1 320px', minWidth: 260, maxWidth: 460 }}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
          <Input
            allowClear
            placeholder="设备IP"
            style={{ width: 170 }}
            value={deviceIp}
            onChange={(event) => setDeviceIp(event.target.value)}
          />
          <Input
            allowClear
            placeholder="接口"
            style={{ width: 170 }}
            value={interfaceName}
            onChange={(event) => setInterfaceName(event.target.value)}
          />
          <Select
            allowClear
            showSearch
            style={{ width: 170 }}
            placeholder="全部厂商"
            value={vendorName || undefined}
            options={vendorOptions}
            optionFilterProp="label"
            onChange={(value) => setVendorName(value || '')}
          />
          <Space size={10} wrap style={{ marginLeft: 'auto' }}>
            {loading ? <ReloadOutlined spin style={{ color: '#1677ff' }} /> : null}
            <Dropdown
              trigger={['click']}
              dropdownRender={() => (
                <Card size="small" bodyStyle={{ padding: 10, width: 260 }}>
                  <Space direction="vertical" size={8} style={{ width: '100%' }}>
                    <Text type="secondary" style={{ fontSize: 12 }}>
                      勾选显示列；拖动已显示列可调整左右顺序
                    </Text>
                    {orderedColumnOptions.map((item) => {
                      const checked = visibleColumnKeys.includes(item.value)
                      return (
                        <div
                          key={item.value}
                          draggable={checked}
                          onDragStart={() => {
                            if (checked) setDraggingColumnKey(item.value)
                          }}
                          onDragOver={(event) => {
                            if (checked && draggingColumnKey) event.preventDefault()
                          }}
                          onDrop={(event) => {
                            event.preventDefault()
                            if (checked && draggingColumnKey) moveVisibleColumn(draggingColumnKey, item.value)
                            setDraggingColumnKey(null)
                          }}
                          onDragEnd={() => setDraggingColumnKey(null)}
                          style={{
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'space-between',
                            gap: 8,
                            padding: '7px 9px',
                            borderRadius: 8,
                            border: checked ? '1px solid #d6e4ff' : '1px solid #f0f0f0',
                            background: draggingColumnKey === item.value ? '#e6f4ff' : checked ? '#f8fbff' : '#fafafa',
                            cursor: checked ? 'grab' : 'default',
                            opacity: checked ? 1 : 0.65,
                          }}
                        >
                          <Checkbox checked={checked} onChange={(event) => toggleColumnVisible(item.value, event.target.checked)}>
                            {columnLabelMap.get(item.value) || item.label}
                          </Checkbox>
                          {checked ? <Text type="secondary" style={{ fontSize: 12 }}>拖动</Text> : null}
                        </div>
                      )
                    })}
                  </Space>
                </Card>
              )}
            >
              <Button>显示/隐藏列</Button>
            </Dropdown>
            <Button icon={<ReloadOutlined />} onClick={() => loadData(page, pageSize)} disabled={!controllerId} loading={loading}>
              刷新
            </Button>
          </Space>
        </div>
        <Space wrap style={{ marginTop: 12 }}>
          <Tag color="blue">模块 {total}</Tag>
          <Tag>当前页 {items.length}</Tag>
          <Text type="secondary">数据窗口：最近3小时；只用于向控制器读取当前健康分析缓存，不展示历史趋势。</Text>
        </Space>
      </Card>

      <Card title={`模块信息查询${total ? `（共 ${total} 条）` : ''}`} bodyStyle={{ padding: 0 }}>
        <Table
          rowKey={(record) => `${record.assetId || getDeviceIp(record)}-${record.ifIndex || getInterfaceName(record)}-${record.serialNumber || ''}`}
          loading={loading}
          dataSource={items}
          scroll={{ x: 1900, y: 'calc(100vh - 360px)' }}
          pagination={{
            current: page,
            pageSize,
            total,
            showSizeChanger: true,
            pageSizeOptions: [20, 50, 100, 200],
            showTotal: (count, range) => `第 ${range[0]}-${range[1]} 条 / 共 ${count} 条`,
            onChange: loadData,
            onShowSizeChange: loadData,
          }}
          columns={visibleColumns}
        />
      </Card>
    </Space>
  )
}

export default ModuleInfoQuery
