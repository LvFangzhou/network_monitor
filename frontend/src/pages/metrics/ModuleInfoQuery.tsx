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
const MODULE_REFRESH_INTERVAL_MS = 600 * 1000
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

type MetricRange = { min: number; max: number; source: string }
type OpticalThresholds = {
  rxPower?: MetricRange
  txPower?: MetricRange
  temperature?: MetricRange
  voltage?: MetricRange
}

const formatNumber = (value?: number | string | null) => {
  if (value === undefined || value === null || value === '') return null
  const numeric = Number(value)
  return Number.isFinite(numeric) ? numeric : null
}

const isInvalidOpticalRawValue = (value: number | null) => {
  if (value === null) return true
  // 控制器/设备常用 int32 最大值作为“未采集到/无效值”占位，不能参与换算和阈值判断。
  return value >= 2147483647 || value <= -2147483648
}

const normalizePower = (value?: number | string | null) => {
  const numeric = formatNumber(value)
  if (numeric === null || isInvalidOpticalRawValue(numeric)) return null
  // 控制器不同型号返回并不完全一致：
  // 1) 127 / 237 这种小正数多为 0.01dBm，表示 1.27 / 2.37dBm；
  // 2) -1710 这种负数多为 0.01dBm，表示 -17.10dBm；
  // 3) 7300 / 8200 / 31622 这种大正数多为 0.1uW 线性功率。
  if (numeric > 1000) return 10 * Math.log10(numeric / 10000)
  if (Math.abs(numeric) > 100) return numeric / 100
  if (Number.isInteger(numeric)) return numeric / 100
  return numeric
}

const normalizeDisplayPower = (value?: number | string | null) => {
  const normalized = normalizePower(value)
  if (normalized === null) return null
  // 光模块通道功率正常不会达到这个量级；超过范围通常仍是厂商占位/异常编码。
  if (normalized > 30 || normalized < -60) return null
  return normalized
}

const normalizeControllerPowerThreshold = (value?: number | string | null) => {
  const numeric = formatNumber(value)
  if (numeric === null || isInvalidOpticalRawValue(numeric)) return null
  if (numeric > 0 && Math.abs(numeric) > 1000) return 10 * Math.log10(numeric / 10000)
  return normalizePower(numeric)
}

const normalizeTemperature = (value?: number | string | null) => {
  const numeric = formatNumber(value)
  if (numeric === null || isInvalidOpticalRawValue(numeric)) return null
  return Math.abs(numeric) > 1000 ? numeric / 1000 : numeric
}

const normalizeVoltage = (value?: number | string | null) => {
  const numeric = formatNumber(value)
  if (numeric === null || isInvalidOpticalRawValue(numeric)) return null
  if (Math.abs(numeric) > 1000) return numeric / 10000
  return numeric
}

const range = (min: number, max: number, source: string): MetricRange => ({ min, max, source })

const H3C_OPTICAL_RANGES: Array<{ match: (record: any) => boolean; rx: MetricRange; tx: MetricRange }> = [
  // 400G，来源：H3C 400G 系列光模块接口指标表
  { match: (r) => isSpeed(r, 400) && hasAny(r, ['SR8']), tx: range(-6.5, 4, 'H3C 400G SR8'), rx: range(-8.4, 4, 'H3C 400G SR8') },
  { match: (r) => isSpeed(r, 400) && hasAny(r, ['SR4']), tx: range(-4.6, 4, 'H3C 400G SR4'), rx: range(-6.4, 4, 'H3C 400G SR4') },
  { match: (r) => isSpeed(r, 400) && hasAny(r, ['VR4']), tx: range(-4.6, 4, 'H3C 400G VR4'), rx: range(-6.3, 4, 'H3C 400G VR4') },
  { match: (r) => isSpeed(r, 400) && hasAny(r, ['DR4']), tx: range(-2.9, 4, 'H3C 400G DR4'), rx: range(-5.9, 4, 'H3C 400G DR4') },
  { match: (r) => isSpeed(r, 400) && hasAny(r, ['FR4']), tx: range(-3.2, 4.4, 'H3C 400G FR4'), rx: range(-7.3, 4.4, 'H3C 400G FR4') },
  { match: (r) => isSpeed(r, 400) && hasAny(r, ['LR4']), tx: range(-2.7, 5.1, 'H3C 400G LR4'), rx: range(-9, 5.1, 'H3C 400G LR4') },
  { match: (r) => isSpeed(r, 400) && hasAny(r, ['LR8']), tx: range(-2.8, 5.3, 'H3C 400G LR8'), rx: range(-9.1, 5.3, 'H3C 400G LR8') },

  // 200G，来源：H3C 200G 系列光模块接口指标表
  { match: (r) => isSpeed(r, 200) && hasAny(r, ['SR4']), tx: range(-6.5, 4, 'H3C 200G SR4'), rx: range(-8.4, 4, 'H3C 200G SR4') },
  { match: (r) => isSpeed(r, 200) && hasAny(r, ['FR4']), tx: range(-4.2, 4.7, 'H3C 200G FR4'), rx: range(-8.2, 4.7, 'H3C 200G FR4') },

  // 100G/50G，来源：H3C 100G/50G 系列光模块接口指标表，按常见型号精确匹配
  { match: (r) => isSpeed(r, 100) && hasAny(r, ['ZR4']), tx: range(2, 6.5, 'H3C 100G ZR4'), rx: range(-28, -7, 'H3C 100G ZR4') },
  { match: (r) => isSpeed(r, 100) && hasAny(r, ['ER4L']), tx: range(0.5, 4.5, 'H3C 100G ER4L'), rx: range(-20.5, -1.9, 'H3C 100G ER4L') },
  { match: (r) => isSpeed(r, 100) && hasAny(r, ['ER4']), tx: range(-2.9, 2.9, 'H3C 100G ER4'), rx: range(-20.9, -3.5, 'H3C 100G ER4') },
  { match: (r) => isSpeed(r, 100) && hasAny(r, ['LR4L', 'CWDM4']), tx: range(-6.5, 2.5, 'H3C 100G LR4L/CWDM4'), rx: range(-11.5, 2.5, 'H3C 100G LR4L/CWDM4') },
  { match: (r) => isSpeed(r, 100) && hasAny(r, ['LR4']), tx: range(-4.3, 4.5, 'H3C 100G LR4'), rx: range(-10.6, 4.5, 'H3C 100G LR4') },
  { match: (r) => isSpeed(r, 100) && hasAny(r, ['PSM4']), tx: range(-9.4, 2, 'H3C 100G PSM4'), rx: range(-12.66, 2, 'H3C 100G PSM4') },
  { match: (r) => isSpeed(r, 100) && hasAny(r, ['DR1']), tx: range(-2.9, 4, 'H3C 100G DR1'), rx: range(-5.9, 4, 'H3C 100G DR1') },
  { match: (r) => isSpeed(r, 100) && hasAny(r, ['FR1']), tx: range(-3.1, 4, 'H3C 100G FR1'), rx: range(-7.1, 4, 'H3C 100G FR1') },
  { match: (r) => isSpeed(r, 100) && hasAny(r, ['LR1']), tx: range(-1.9, 4.8, 'H3C 100G LR1'), rx: range(-8.2, 4.8, 'H3C 100G LR1') },
  { match: (r) => isSpeed(r, 100) && hasAny(r, ['BIDI']), tx: range(-6.4, 4, 'H3C 100G BIDI'), rx: range(-8.2, 4, 'H3C 100G BIDI') },
  { match: (r) => isSpeed(r, 100) && hasAny(r, ['SWDM4']), tx: range(-7.5, 2.4, 'H3C 100G SWDM4'), rx: range(-9.5, 3.4, 'H3C 100G SWDM4') },
  { match: (r) => isSpeed(r, 100) && hasAny(r, ['SR4', 'ESR4', 'ESR']), tx: range(-8.4, 2.4, 'H3C 100G SR4/eSR4'), rx: range(-10.3, 2.4, 'H3C 100G SR4/eSR4') },
  { match: (r) => isSpeed(r, 50) && hasAny(r, ['ER']), tx: range(0.4, 6.6, 'H3C 50G ER'), rx: range(-17.6, -3.4, 'H3C 50G ER') },
  { match: (r) => isSpeed(r, 50), tx: range(-4.5, 4.2, 'H3C 50G LR'), rx: range(-10.8, 4.2, 'H3C 50G LR') },

  // 10G 常见 H3C 光模块兜底；如果控制器返回了模块自身阈值，会优先使用控制器阈值。
  { match: (r) => isSpeed(r, 10) && hasAny(r, ['ZR']), tx: range(0, 4, 'H3C 10G ZR 常用范围'), rx: range(-24, -7, 'H3C 10G ZR 常用范围') },
  { match: (r) => isSpeed(r, 10) && hasAny(r, ['ER']), tx: range(-1, 4, 'H3C 10G ER 常用范围'), rx: range(-15.8, -1, 'H3C 10G ER 常用范围') },
  { match: (r) => isSpeed(r, 10) && hasAny(r, ['LR', 'LXM']), tx: range(-8.2, 0.5, 'H3C 10G LR 常用范围'), rx: range(-14.4, 0.5, 'H3C 10G LR 常用范围') },
  { match: (r) => isSpeed(r, 10) && hasAny(r, ['SR', '850']), tx: range(-7.3, -1, 'H3C 10G SR 常用范围'), rx: range(-9.9, -1, 'H3C 10G SR 常用范围') },

  // 25G，来源：H3C 25G 系列光模块接口指标表
  { match: (r) => isSpeed(r, 25) && hasAny(r, ['CSR']), tx: range(-6.4, 2.4, 'H3C 25G CSR'), rx: range(-10.3, 2.4, 'H3C 25G CSR') },
  { match: (r) => isSpeed(r, 25) && hasAny(r, ['LR']), tx: range(-7, 2, 'H3C 25G LR'), rx: range(-13.3, 2, 'H3C 25G LR') },
  { match: (r) => isSpeed(r, 25) && hasAny(r, ['SR']), tx: range(-8.4, 2.4, 'H3C 25G SR'), rx: range(-10.3, 2.4, 'H3C 25G SR') },
]

const isPowerAbnormal = (value: number | null, targetRange?: MetricRange) =>
  value !== null && Boolean(targetRange) && (value < targetRange!.min || value > targetRange!.max)
const isMetricAbnormal = (value: number | null, targetRange?: MetricRange) =>
  value !== null && Boolean(targetRange) && (value < targetRange!.min || value > targetRange!.max)

const MetricText = ({ value, unit, danger, targetRange }: { value: number | null; unit: string; danger: boolean; targetRange?: MetricRange }) => {
  if (value === null) {
    return (
      <Tooltip title="控制器返回无效占位值，通常表示该模块本次未采集到有效数据">
        <Text type="secondary">未读到</Text>
      </Tooltip>
    )
  }
  return (
    <Tooltip title={targetRange ? `${targetRange.source} 正常范围：${targetRange.min}～${targetRange.max} ${unit}` : undefined}>
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
const isInterfaceUp = (record: any) => Number(record?.ifOperStatus) === 1
const opticalText = (record: any) =>
  [
    record?.transceiveType,
    record?.model,
    record?.vendorName,
    record?.ifDesc,
    record?.ifDescRaw,
  ].map((item) => String(item || '').toUpperCase()).join(' ')

const getSpeedGbps = (record: any) => {
  const raw = formatNumber(record?.transceiverSpeed)
  if (raw !== null && raw > 0) {
    if (raw >= 1000) return raw / 1024
    return raw
  }
  const text = opticalText(record)
  const matched = text.match(/(\d+)\s*G/)
  return matched ? Number(matched[1]) : null
}

function isSpeed(record: any, speed: number) {
  const actual = getSpeedGbps(record)
  if (actual === null) return false
  return Math.abs(actual - speed) < Math.max(2, speed * 0.08)
}

function hasAny(record: any, keywords: string[]) {
  const text = opticalText(record)
  return keywords.some((keyword) => text.includes(keyword.toUpperCase()))
}

const getH3cPowerThresholds = (record: any) => {
  const controllerThresholds = getControllerPowerThresholds(record)
  if (controllerThresholds.rxPower && controllerThresholds.txPower) return controllerThresholds
  const matched = H3C_OPTICAL_RANGES.find((item) => item.match(record))
  if (matched) return { rxPower: matched.rx, txPower: matched.tx }
  if (isSpeed(record, 400)) return { txPower: range(-6.5, 5.3, 'H3C 400G 通用范围'), rxPower: range(-9.1, 5.3, 'H3C 400G 通用范围') }
  if (isSpeed(record, 200)) return { txPower: range(-6.5, 4.7, 'H3C 200G 通用范围'), rxPower: range(-8.4, 4.7, 'H3C 200G 通用范围') }
  if (isSpeed(record, 100)) return { txPower: range(-9.4, 6.5, 'H3C 100G 通用范围'), rxPower: range(-28, 4.8, 'H3C 100G 通用范围') }
  if (isSpeed(record, 50)) return { txPower: range(-4.5, 6.6, 'H3C 50G 通用范围'), rxPower: range(-17.6, 4.2, 'H3C 50G 通用范围') }
  if (isSpeed(record, 25)) return { txPower: range(-8.4, 2.4, 'H3C 25G 通用范围'), rxPower: range(-13.3, 2.4, 'H3C 25G 通用范围') }
  if (isSpeed(record, 10)) return { txPower: range(-8.2, 4, 'H3C 10G 通用范围'), rxPower: range(-24, 0.5, 'H3C 10G 通用范围') }
  return {}
}

const getControllerPowerThresholds = (record: any) => {
  const rxLo = normalizeControllerPowerThreshold(record?.rcvPwrLoAlarm)
  const rxHi = normalizeControllerPowerThreshold(record?.rcvPwrHiAlarm)
  const txLo = normalizeControllerPowerThreshold(record?.pwrOutLoAlarm)
  const txHi = normalizeControllerPowerThreshold(record?.pwrOutHiAlarm)
  return {
    rxPower: rxLo !== null && rxHi !== null && rxLo < rxHi ? range(rxLo, rxHi, '控制器模块 RX 告警阈值') : undefined,
    txPower: txLo !== null && txHi !== null && txLo < txHi ? range(txLo, txHi, '控制器模块 TX 告警阈值') : undefined,
  }
}

const getTemperatureThreshold = (record: any): MetricRange => {
  if (isSpeed(record, 200)) return range(0, 75, 'H3C 200G 适宜工作温度')
  return range(0, 70, 'H3C 25G/100G/400G 适宜工作温度')
}

const getVoltageThreshold = (record: any): MetricRange => {
  const lo = normalizeVoltage(record?.vccLoAlarm)
  const hi = normalizeVoltage(record?.vccHiAlarm)
  if (lo !== null && hi !== null && lo < hi) return range(lo, hi, '控制器模块 VCC 告警阈值')
  return range(2.97, 3.63, '3.3V 光模块常用 VCC 范围')
}

const getOpticalThresholds = (record: any): OpticalThresholds => ({
  ...getH3cPowerThresholds(record),
  temperature: getTemperatureThreshold(record),
  voltage: getVoltageThreshold(record),
})

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

  useEffect(() => {
    if (!controllerId) return undefined
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') {
        loadData(page, pageSize)
      }
    }, MODULE_REFRESH_INTERVAL_MS)
    return () => window.clearInterval(timer)
  }, [controllerId, page, pageSize, search, deviceIp, interfaceName, vendorName])

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
    { title: '运行状态', key: 'ifOperStatus', dataIndex: 'ifOperStatus', width: 110, sorter: (a: any, b: any) => Number(a.ifOperStatus || 0) - Number(b.ifOperStatus || 0), render: (value: any) => Number(value) === 1 ? <Tag color="green">UP</Tag> : <Tag color="red">DOWN</Tag> },
    { title: '管理状态', key: 'adminStatus', dataIndex: 'adminStatus', width: 110, sorter: (a: any, b: any) => Number(a.adminStatus || 0) - Number(b.adminStatus || 0), render: (value: any) => Number(value) === 1 ? <Tag color="green">UP</Tag> : <Tag>DOWN</Tag> },
    { title: '厂商', key: 'vendorName', dataIndex: 'vendorName', width: 150, ellipsis: true, sorter: (a: any, b: any) => compareNatural(a.vendorName, b.vendorName), render: (value: string) => value || '-' },
    { title: '序列号', key: 'serialNumber', dataIndex: 'serialNumber', width: 180, ellipsis: true, sorter: (a: any, b: any) => compareNatural(a.serialNumber, b.serialNumber), render: (value: string) => value || '-' },
    { title: '类型', key: 'transceiveType', dataIndex: 'transceiveType', width: 160, ellipsis: true, sorter: (a: any, b: any) => compareNatural(a.transceiveType, b.transceiveType), render: (value: string) => value || '-' },
    { title: '速率', key: 'transceiverSpeed', dataIndex: 'transceiverSpeed', width: 120, sorter: (a: any, b: any) => compareNatural(a.transceiverSpeed, b.transceiverSpeed), render: (value: string) => value || '-' },
    {
      title: '收光',
      key: 'curRxPower',
      dataIndex: 'curRxPower',
      width: 120,
      sorter: (a: any, b: any) => (normalizeDisplayPower(a.curRxPower) ?? -999) - (normalizeDisplayPower(b.curRxPower) ?? -999),
      render: (value: any, record: any) => {
        const thresholds = getOpticalThresholds(record)
        const normalized = normalizeDisplayPower(value)
        return <MetricText value={normalized} unit="dBm" targetRange={thresholds.rxPower} danger={isInterfaceUp(record) && isPowerAbnormal(normalized, thresholds.rxPower)} />
      },
    },
    {
      title: '发光',
      key: 'curTxPower',
      dataIndex: 'curTxPower',
      width: 120,
      sorter: (a: any, b: any) => (normalizeDisplayPower(a.curTxPower) ?? -999) - (normalizeDisplayPower(b.curTxPower) ?? -999),
      render: (value: any, record: any) => {
        const thresholds = getOpticalThresholds(record)
        const normalized = normalizeDisplayPower(value)
        return <MetricText value={normalized} unit="dBm" targetRange={thresholds.txPower} danger={isInterfaceUp(record) && isPowerAbnormal(normalized, thresholds.txPower)} />
      },
    },
    {
      title: '温度',
      key: 'curTemperature',
      dataIndex: 'curTemperature',
      width: 110,
      sorter: (a: any, b: any) => (normalizeTemperature(a.curTemperature) ?? -999) - (normalizeTemperature(b.curTemperature) ?? -999),
      render: (value: any, record: any) => {
        const thresholds = getOpticalThresholds(record)
        const normalized = normalizeTemperature(value)
        return <MetricText value={normalized} unit="℃" targetRange={thresholds.temperature} danger={isMetricAbnormal(normalized, thresholds.temperature)} />
      },
    },
    {
      title: '电压',
      key: 'curVoltage',
      dataIndex: 'curVoltage',
      width: 110,
      sorter: (a: any, b: any) => (normalizeVoltage(a.curVoltage) ?? -999) - (normalizeVoltage(b.curVoltage) ?? -999),
      render: (value: any, record: any) => {
        const thresholds = getOpticalThresholds(record)
        const normalized = normalizeVoltage(value)
        return <MetricText value={normalized} unit="V" targetRange={thresholds.voltage} danger={isMetricAbnormal(normalized, thresholds.voltage)} />
      },
    },
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
          <Text type="secondary">数据窗口：最近3小时；页面每600秒自动刷新一次，仅在当前页面可见时刷新。</Text>
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
