import { useEffect, useMemo, useRef, useState } from 'react'
import {
  AutoComplete,
  Button,
  Card,
  Empty,
  Radio,
  Select,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
  message,
  theme,
} from 'antd'
import { HolderOutlined, LineChartOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { useLocation, useNavigate } from 'react-router-dom'
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from 'recharts'
import {
  getMonitorDeviceByIp,
  getMonitorDeviceInterfaces,
  getMonitorInterfaceHistory,
  getMonitorInterfaceQueueHistory,
  searchMonitorDevices,
  type MonitorDynamicSeries,
  type MonitorDevice,
  type MonitorDeviceSearchOption,
  type MonitorHistoryPoint,
  type MonitorInterface,
} from '../../api/metrics'
import { useThemeStore } from '../../store/theme'

const { Text } = Typography

type MonitorGroupKey = 'traffic' | 'utilization' | 'discards' | 'errors' | 'buffer' | 'queueDropGrowth' | 'pfcGrowth' | 'ecnGrowth'

type MonitorGroup = {
  key: MonitorGroupKey
  label: string
  unit: 'bps' | 'percent' | 'count'
  series: MonitorDynamicSeries[]
}

type DisplayScale = {
  divisor: number
  suffix: string
}

const BPS_UNIT_THRESHOLDS = {
  kbps: 1_000,
  mbps: 1_000_000,
  gbps: 1_000_000_000,
}

type ChartPoint = {
  timestamp: number
  sample_seconds?: number | null
  in_octets?: number | null
  out_octets?: number | null
  in_bps?: number | null
  out_bps?: number | null
  in_utilization_percent?: number | null
  out_utilization_percent?: number | null
  in_discards?: number | null
  out_discards?: number | null
  in_errors?: number | null
  out_errors?: number | null
  in_discards_delta?: number | null
  out_discards_delta?: number | null
  in_errors_delta?: number | null
  out_errors_delta?: number | null
  queue_egress_dropped_pkts_delta?: number | null
  queue_ingress_dropped_pkts_delta?: number | null
  pfc_rx_pkts_delta?: number | null
  pfc_tx_pkts_delta?: number | null
  ecn_marked_pkts_delta?: number | null
  buffer_usage?: number | null
  speed_bps?: number | null
  [key: string]: number | string | null | undefined
}

type SelectedTarget = {
  key: string
  device: MonitorDevice
  interface: MonitorInterface
}

type ZoomRange = {
  start?: number
  end?: number
  rangeValue?: string
  interval?: string
}

const DEVICE_SEARCH_DEBOUNCE_MS = 300
const MONITOR_HISTORY_CACHE_KEY = 'monitor_center_history_v7'
const MONITOR_CENTER_STATE_KEY = 'monitor_center_state_v1'
const HISTORY_CACHE_RETENTION_MS = 7 * 24 * 60 * 60 * 1000
const HISTORY_REQUEST_CACHE_MS = 8 * 1000

const RANGE_OPTIONS = [
  { value: '-10m', label: '过去10分钟' },
  { value: '-30m', label: '过去30分钟' },
  { value: '-1h', label: '过去1小时' },
  { value: '-6h', label: '过去6小时' },
  { value: '-12h', label: '过去12小时' },
  { value: '-24h', label: '过去24小时' },
  { value: '-3d', label: '过去3天' },
  { value: '-7d', label: '过去7天' },
]

const isValidRangeValue = (value?: string) => RANGE_OPTIONS.some((item) => item.value === value)

const REFRESH_OPTIONS = [
  { value: 10, label: '每10秒刷新' },
  { value: 30, label: '每30秒刷新' },
  { value: 60, label: '每60秒刷新' },
]

const MONITOR_GROUPS: MonitorGroup[] = [
  {
    key: 'traffic',
    label: '交换机端口出入流量',
    unit: 'bps',
    series: [
      { key: 'in_bps', label: '交换机端口入流量', color: '#70d34f' },
      { key: 'out_bps', label: '交换机端口出流量', color: '#f4d000' },
    ],
  },
  {
    key: 'utilization',
    label: '交换机端口出入带宽使用率',
    unit: 'percent',
    series: [
      { key: 'in_utilization_percent', label: '交换机端口入带宽使用率', color: '#13c2c2' },
      { key: 'out_utilization_percent', label: '交换机端口出带宽使用率', color: '#fa8c16' },
    ],
  },
  {
    key: 'discards',
    label: '交换机端口出入丢弃包增长',
    unit: 'count',
    series: [
      { key: 'in_discards_delta', label: '交换机端口入丢弃包增长', color: '#eb2f96' },
      { key: 'out_discards_delta', label: '交换机端口出丢弃包增长', color: '#f5222d' },
    ],
  },
  {
    key: 'errors',
    label: '交换机端口错误包增长',
    unit: 'count',
    series: [
      { key: 'in_errors_delta', label: '交换机端口入错误包增长', color: '#2f54eb' },
      { key: 'out_errors_delta', label: '交换机端口出错误包增长', color: '#fa541c' },
    ],
  },
  {
    key: 'queueDropGrowth',
    label: 'AsterNOS队列丢包增长',
    unit: 'count',
    series: [
      { key: 'queue_ingress_dropped_pkts_delta', label: '队列入方向丢包增长', color: '#fa541c' },
      { key: 'queue_egress_dropped_pkts_delta', label: '队列出方向丢包增长', color: '#eb2f96' },
    ],
  },
  {
    key: 'pfcGrowth',
    label: 'AsterNOS PFC包增长',
    unit: 'count',
    series: [
      { key: 'pfc_rx_pkts_delta', label: 'PFC RX包增长', color: '#722ed1' },
      { key: 'pfc_tx_pkts_delta', label: 'PFC TX包增长', color: '#13c2c2' },
    ],
  },
  {
    key: 'ecnGrowth',
    label: 'AsterNOS ECN标记包增长',
    unit: 'count',
    series: [{ key: 'ecn_marked_pkts_delta', label: 'ECN标记包增长', color: '#fa8c16' }],
  },
  {
    key: 'buffer',
    label: '交换机端口输出队列长度',
    unit: 'count',
    series: [{ key: 'buffer_usage', label: '交换机端口输出队列长度', color: '#52c41a' }],
  },
]

const getMonitorGroupUnitLabel = (group: MonitorGroup, scale: DisplayScale) => {
  if (group.unit === 'bps') return '自适应'
  if (group.unit === 'percent') return '%'
  if (group.key === 'discards' || group.key === 'errors') return '包/采集周期'
  if (group.key === 'queueDropGrowth' || group.key === 'pfcGrowth' || group.key === 'ecnGrowth') return '包/采集周期'
  if (group.key === 'buffer') return '队列包数'
  return scale.suffix || 'count'
}

const isQueueDetailGroup = (key: MonitorGroupKey) =>
  key === 'queueDropGrowth' || key === 'pfcGrowth' || key === 'ecnGrowth'

const getDisplayScale = (unit: MonitorGroup['unit'], value?: number | null): DisplayScale => {
  const safeValue = Math.abs(value ?? 0)

  if (unit === 'bps') {
    if (safeValue >= BPS_UNIT_THRESHOLDS.gbps) return { divisor: 1_000_000_000, suffix: 'Gbps' }
    if (safeValue >= BPS_UNIT_THRESHOLDS.mbps) return { divisor: 1_000_000, suffix: 'Mbps' }
    if (safeValue >= BPS_UNIT_THRESHOLDS.kbps) return { divisor: 1_000, suffix: 'Kbps' }
    return { divisor: 1, suffix: 'bps' }
  }

  if (unit === 'count') {
    if (safeValue >= 1_000_000_000) return { divisor: 1_000_000_000, suffix: 'B' }
    if (safeValue >= 1_000_000) return { divisor: 1_000_000, suffix: 'M' }
    if (safeValue >= 1_000) return { divisor: 1_000, suffix: 'K' }
    return { divisor: 1, suffix: '' }
  }

  return { divisor: 1, suffix: '%' }
}

const getAxisPrecision = (unit: MonitorGroup['unit'], scale: DisplayScale, maxValue?: number | null) => {
  const safeValue = Math.abs(maxValue ?? 0) / scale.divisor

  if (unit === 'bps') {
    if (scale.suffix === 'bps') {
      if (safeValue < 100) return 0
      if (safeValue < 1000) return 1
      return 0
    }
    if (safeValue < 10) return 2
    if (safeValue < 100) return 1
    return 0
  }

  if (unit === 'count') {
    if (safeValue < 10) return 2
    if (safeValue < 100) return 1
    return 0
  }

  return 0
}

const formatMetricValue = (
  value: number | null | undefined,
  unit: MonitorGroup['unit'],
  scale?: DisplayScale
) => {
  if (value === undefined || value === null) return '-'
  if (unit === 'percent') return `${value.toFixed(2)}%`

  const resolvedScale = unit === 'bps' ? getDisplayScale(unit, value) : (scale || getDisplayScale(unit, value))
  const scaledValue = value / resolvedScale.divisor
  const precision = getAxisPrecision(unit, resolvedScale, value)

  if (unit === 'bps') {
    return `${scaledValue.toFixed(precision)} ${resolvedScale.suffix}`
  }

  if (resolvedScale.suffix) {
    return `${scaledValue.toFixed(precision)} ${resolvedScale.suffix}`
  }

  return scaledValue.toLocaleString()
}

const formatAxisTick = (
  value: number,
  unit: MonitorGroup['unit'],
  scale?: DisplayScale,
  maxValue?: number | null
) => {
  if (unit === 'percent') return `${value.toFixed(0)}%`

  const resolvedScale = scale || getDisplayScale(unit, value)
  const scaledValue = value / resolvedScale.divisor
  const precision = getAxisPrecision(unit, resolvedScale, maxValue ?? value)

  if (resolvedScale.suffix) {
    return `${scaledValue.toFixed(precision)}\u00a0${resolvedScale.suffix}`
  }

  return `${scaledValue.toFixed(precision)}`
}

const getLinearYAxisTicks = (values: number[], divisions = 5, fixedMax?: number | null) => {
  const numericValues = values.filter((value) => Number.isFinite(value) && value >= 0)
  const dataMax = numericValues.length ? Math.max(...numericValues) : 1
  const niceMax = Math.max(getNiceAxisMax(dataMax), 1)
  const maxTick = fixedMax && fixedMax > 0 ? Math.min(niceMax, fixedMax) : niceMax
  const step = maxTick / divisions
  const ticks = Array.from({ length: divisions + 1 }, (_, index) => Number((step * index).toFixed(6)))
  return {
    ticks,
    domain: [0, maxTick] as [number, number],
  }
}

const getNiceAxisMax = (value: number) => {
  if (!Number.isFinite(value) || value <= 0) return 1
  const target = value * 1.08
  const exponent = Math.floor(Math.log10(target))
  const base = 10 ** exponent
  const normalized = target / base
  const niceNormalized = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 5 ? 5 : 10
  return niceNormalized * base
}

const toChartPoint = (item: MonitorHistoryPoint | { _time?: string } & Record<string, any>): ChartPoint => {
  const rawTime = item._time || new Date().toISOString()
  const normalizedTime =
    typeof rawTime === 'string' && rawTime.includes('T') && !/[zZ]|[+\-]\d{2}:\d{2}$/.test(rawTime)
      ? `${rawTime}Z`
      : rawTime
  const time = dayjs(normalizedTime)
  return {
    timestamp: time.valueOf(),
    sample_seconds: item.sample_seconds ?? null,
    in_octets: item.in_octets ?? null,
    out_octets: item.out_octets ?? null,
    in_bps: item.in_bps ?? null,
    out_bps: item.out_bps ?? null,
    in_utilization_percent: item.in_utilization_percent ?? null,
    out_utilization_percent: item.out_utilization_percent ?? null,
    in_discards: item.in_discards ?? null,
    out_discards: item.out_discards ?? null,
    in_errors: item.in_errors ?? null,
    out_errors: item.out_errors ?? null,
    in_discards_delta: item.in_discards_delta ?? null,
    out_discards_delta: item.out_discards_delta ?? null,
    in_errors_delta: item.in_errors_delta ?? null,
    out_errors_delta: item.out_errors_delta ?? null,
    queue_egress_dropped_pkts_delta: item.queue_egress_dropped_pkts_delta ?? null,
    queue_ingress_dropped_pkts_delta: item.queue_ingress_dropped_pkts_delta ?? null,
    pfc_rx_pkts_delta: item.pfc_rx_pkts_delta ?? null,
    pfc_tx_pkts_delta: item.pfc_tx_pkts_delta ?? null,
    ecn_marked_pkts_delta: item.ecn_marked_pkts_delta ?? null,
    buffer_usage: item.buffer_usage ?? null,
    speed_bps: item.speed_bps ?? null,
  }
}

const formatXAxisTick = (timestamp: number, rangeValue: string, domain?: [number, number]) => {
  const time = dayjs(timestamp)
  const spanMs =
    domain && Number.isFinite(domain[0]) && Number.isFinite(domain[1]) && domain[1] > domain[0]
      ? domain[1] - domain[0]
      : getRangeWindowMs(rangeValue)
  const shouldIncludeDate = spanMs > 24 * 60 * 60 * 1000

  if (shouldIncludeDate) {
    if (spanMs >= 3 * 24 * 60 * 60 * 1000) return time.format('MM-DD HH:mm')
    return time.format('MM-DD HH:mm')
  }
  if (rangeValue === '-10m') return time.format('HH:mm:ss')
  if (rangeValue === '-30m' || rangeValue === '-1h') return time.format('HH:mm')
  if (rangeValue === '-6h' || rangeValue === '-12h' || rangeValue === '-24h') return time.format('HH:mm')
  return time.format('MM-DD HH:mm')
}

const getXAxisMinTickGap = (rangeValue: string) => {
  if (rangeValue === '-10m') return 48
  if (rangeValue === '-30m') return 56
  if (rangeValue === '-1h') return 64
  if (rangeValue === '-6h') return 88
  if (rangeValue === '-12h') return 92
  if (rangeValue === '-24h') return 96
  return 110
}

const getXAxisTickCount = (rangeValue: string) => {
  if (rangeValue === '-10m') return 7
  if (rangeValue === '-30m') return 8
  if (rangeValue === '-1h') return 8
  if (rangeValue === '-6h') return 9
  if (rangeValue === '-12h') return 9
  if (rangeValue === '-24h') return 9
  return 8
}

const getLinearXAxisTicks = (domain: [number, number], rangeValue: string) => {
  const [start, end] = domain
  const count = getXAxisTickCount(rangeValue)
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start || count <= 1) {
    return []
  }
  const step = (end - start) / (count - 1)
  return Array.from({ length: count }, (_, index) => Math.round(start + step * index))
}

const getRangeWindowMs = (rangeValue: string) => {
  if (rangeValue === '-10m') return 10 * 60 * 1000
  if (rangeValue === '-30m') return 30 * 60 * 1000
  if (rangeValue === '-1h') return 60 * 60 * 1000
  if (rangeValue === '-6h') return 6 * 60 * 60 * 1000
  if (rangeValue === '-12h') return 12 * 60 * 60 * 1000
  if (rangeValue === '-24h') return 24 * 60 * 60 * 1000
  if (rangeValue === '-3d') return 3 * 24 * 60 * 60 * 1000
  if (rangeValue === '-7d') return 7 * 24 * 60 * 60 * 1000
  return HISTORY_CACHE_RETENTION_MS
}

const getRangeValueForWindowMs = (windowMs: number) => {
  if (windowMs <= getRangeWindowMs('-10m')) return '-10m'
  if (windowMs <= getRangeWindowMs('-30m')) return '-30m'
  if (windowMs <= getRangeWindowMs('-1h')) return '-1h'
  if (windowMs <= getRangeWindowMs('-6h')) return '-6h'
  if (windowMs <= getRangeWindowMs('-12h')) return '-12h'
  if (windowMs <= getRangeWindowMs('-24h')) return '-24h'
  if (windowMs <= getRangeWindowMs('-3d')) return '-3d'
  return '-7d'
}

const parseIntervalMs = (interval: string) => {
  const match = interval.match(/^(\d+)(s|m|h|d)$/)
  if (!match) return 30 * 1000
  const value = Number(match[1])
  if (match[2] === 's') return value * 1000
  if (match[2] === 'm') return value * 60 * 1000
  if (match[2] === 'h') return value * 60 * 60 * 1000
  return value * 24 * 60 * 60 * 1000
}

const getAdaptiveInterval = (rangeValue: string) => {
  const intervalMap: Record<string, string> = {
    '-10m': '10s',
    '-30m': '30s',
    '-1h': '1m',
    '-6h': '5m',
    '-12h': '5m',
    '-24h': '5m',
    '-3d': '5m',
    '-7d': '5m',
  }
  return intervalMap[rangeValue] || '30s'
}

const getAdaptiveRateWindow = (rangeValue: string) => {
  const rateWindowMap: Record<string, string> = {
    '-10m': '5m',
    '-30m': '5m',
    '-1h': '5m',
    '-6h': '5m',
    '-12h': '5m',
    '-24h': '5m',
    '-3d': '5m',
    '-7d': '5m',
  }
  return rateWindowMap[rangeValue] || '5m'
}

const getGapThresholdMs = (rangeValue: string, interval: string, refreshSeconds: number) => {
  const baseThreshold = Math.max(parseIntervalMs(interval) * 1.8, refreshSeconds * 1000 * 2, 30_000)
  const rangeGapMap: Record<string, number> = {
    '-10m': 45 * 1000,
    '-30m': 60 * 1000,
    '-1h': 90 * 1000,
    '-6h': 8 * 60 * 1000,
    '-12h': 8 * 60 * 1000,
    '-24h': 8 * 60 * 1000,
    '-3d': 8 * 60 * 1000,
    '-7d': 8 * 60 * 1000,
  }
  return Math.max(baseThreshold, rangeGapMap[rangeValue] || 8 * 60 * 1000)
}

const getChartDotStride = (rangeValue: string, pointCount: number) => {
  if (pointCount <= 0) return 0
  if (pointCount <= 120) return 1
  if (rangeValue === '-6h') return 2
  if (rangeValue === '-12h') return 4
  if (rangeValue === '-24h') return 6
  if (rangeValue === '-3d') return 24
  if (rangeValue === '-7d') return 48
  return Math.ceil(pointCount / 120)
}

const chartDotProps = { r: 2, strokeWidth: 1.5, fill: '#fff' } as const

const renderSampledDot = (stride: number) => (props: any) => {
  const { cx, cy, index, stroke } = props
  if (!stride || index % stride !== 0 || cx == null || cy == null) {
    return <></>
  }
  return <circle cx={cx} cy={cy} r={chartDotProps.r} stroke={stroke} strokeWidth={chartDotProps.strokeWidth} fill={chartDotProps.fill} />
}

const hasSeriesValue = (point: ChartPoint, series: MonitorGroup['series']) => {
  return series.some((serie) => typeof point[serie.key] === 'number')
}

const buildGapPoint = (timestamp: number, series: MonitorGroup['series'], value: number | null): ChartPoint => {
  const point: ChartPoint = { timestamp }
  for (const serie of series) {
    ;(point as Record<string, unknown>)[serie.key] = value
  }
  return point
}

const insertCollectionGaps = (
  points: ChartPoint[],
  series: MonitorGroup['series'],
  gapThresholdMs: number,
  gapValue: number | null
) => {
  const validPoints = points.filter((point) => hasSeriesValue(point, series))
  if (validPoints.length <= 1) return validPoints

  const result: ChartPoint[] = []
  for (const point of validPoints) {
    const previous = result[result.length - 1]
    const previousSampleMs = typeof previous?.sample_seconds === 'number' ? previous.sample_seconds * 1000 : 0
    const currentSampleMs = typeof point.sample_seconds === 'number' ? point.sample_seconds * 1000 : 0
    const dynamicGapThresholdMs = Math.max(gapThresholdMs, previousSampleMs * 1.6, currentSampleMs * 1.6)
    if (previous && point.timestamp - previous.timestamp > dynamicGapThresholdMs) {
      result.push(buildGapPoint(previous.timestamp + 1, series, gapValue))
      result.push(buildGapPoint(point.timestamp - 1, series, gapValue))
    }
    result.push(point)
  }
  return result
}

const interpolateValue = (
  before: ChartPoint | undefined,
  after: ChartPoint | undefined,
  timestamp: number,
  key: string,
  gapThresholdMs: number
) => {
  const beforeValue = before?.[key]
  const afterValue = after?.[key]
  const beforeNumber = typeof beforeValue === 'number' ? beforeValue : null
  const afterNumber = typeof afterValue === 'number' ? afterValue : null

  if (before && Math.abs(timestamp - before.timestamp) <= 1 && beforeNumber !== null) {
    return beforeNumber
  }
  if (after && Math.abs(after.timestamp - timestamp) <= 1 && afterNumber !== null) {
    return afterNumber
  }
  if (
    before &&
    after &&
    before.timestamp < timestamp &&
    after.timestamp > timestamp &&
    after.timestamp - before.timestamp <= gapThresholdMs &&
    beforeNumber !== null &&
    afterNumber !== null
  ) {
    const ratio = (timestamp - before.timestamp) / (after.timestamp - before.timestamp)
    return beforeNumber + (afterNumber - beforeNumber) * ratio
  }
  if (before && timestamp - before.timestamp <= gapThresholdMs && beforeNumber !== null) {
    return beforeNumber
  }
  if (after && after.timestamp - timestamp <= gapThresholdMs && afterNumber !== null) {
    return afterNumber
  }
  return 0
}

const resampleChartData = (
  points: ChartPoint[],
  series: MonitorGroup['series'],
  domain: [number, number],
  interval: string,
  gapThresholdMs: number,
  unit: MonitorGroup['unit']
) => {
  if (unit !== 'bps') {
    return insertCollectionGaps(points, series, gapThresholdMs, null)
  }

  const validPoints = points
    .filter((point) => hasSeriesValue(point, series))
    .sort((a, b) => a.timestamp - b.timestamp)
  if (!validPoints.length) return []

  const stepMs = parseIntervalMs(interval)
  const start = Math.max(domain[0], Math.ceil(validPoints[0].timestamp / stepMs) * stepMs)
  const end = Math.min(domain[1], Math.floor(validPoints[validPoints.length - 1].timestamp / stepMs) * stepMs)
  if (end < start) return validPoints

  const pointsByTimestamp = new Map(validPoints.map((point) => [point.timestamp, point]))
  const sampled: ChartPoint[] = []
  let cursor = 0

  for (let timestamp = start; timestamp <= end; timestamp += stepMs) {
    while (cursor < validPoints.length && validPoints[cursor].timestamp < timestamp) {
      cursor += 1
    }
    const exact = pointsByTimestamp.get(timestamp)
    const before = exact || validPoints[cursor - 1]
    const after = exact || validPoints[cursor]
    const point: ChartPoint = {
      timestamp,
      sample_seconds: exact?.sample_seconds ?? before?.sample_seconds ?? after?.sample_seconds ?? null,
      speed_bps: exact?.speed_bps ?? before?.speed_bps ?? after?.speed_bps ?? null,
    }

    for (const serie of series) {
      point[serie.key] = exact && typeof exact[serie.key] === 'number'
        ? exact[serie.key] as number
        : interpolateValue(before, after, timestamp, serie.key, gapThresholdMs)
    }

    sampled.push(point)
  }

  return sampled
}

const trimChartPointsToRange = (points: ChartPoint[], rangeValue: string, now = Date.now()) => {
  const threshold = now - getRangeWindowMs(rangeValue)
  return points.filter((point) => point.timestamp >= threshold && point.timestamp <= now)
}

const trimChartPointsToRetention = (points: ChartPoint[], now = Date.now()) => {
  const threshold = now - HISTORY_CACHE_RETENTION_MS
  return points.filter((point) => point.timestamp >= threshold && point.timestamp <= now)
}

const compactChartPoint = (point: ChartPoint): ChartPoint => (
  Object.fromEntries(
    Object.entries(point).filter(([, value]) => value !== null && value !== undefined)
  ) as ChartPoint
)

const targetKey = (deviceId: number, interfaceIndex: number) => `${deviceId}:${interfaceIndex}`

const historyCacheKey = (targetKeyValue: string, rangeValue: string, interval: string, monitorKey = 'traffic') =>
  `${targetKeyValue}|${rangeValue}|${interval}|${monitorKey}`

const zoomHistoryCacheKey = (
  targetKeyValue: string,
  start: number,
  end: number,
  interval: string,
  monitorKey = 'traffic'
) => `${targetKeyValue}|zoom:${Math.round(start)}:${Math.round(end)}|${interval}|${monitorKey}`

const loadCachedHistoryMap = (): Record<string, ChartPoint[]> => {
  if (typeof window === 'undefined') return {}
  try {
    const raw = window.localStorage.getItem(MONITOR_HISTORY_CACHE_KEY)
    if (!raw) return {}
    const parsed = JSON.parse(raw) as Record<string, ChartPoint[]>
    const threshold = Date.now() - HISTORY_CACHE_RETENTION_MS
    return Object.fromEntries(
      Object.entries(parsed).map(([key, points]) => [
        key,
        (points || []).filter((point) => point.timestamp >= threshold).map(compactChartPoint),
      ])
    )
  } catch {
    return {}
  }
}

const persistHistoryMap = (historyMap: Record<string, ChartPoint[]>) => {
  if (typeof window === 'undefined') return
  const threshold = Date.now() - HISTORY_CACHE_RETENTION_MS
  const trimmed = Object.fromEntries(
    Object.entries(historyMap).map(([key, points]) => [
      key,
      (points || []).filter((point) => point.timestamp >= threshold).map(compactChartPoint),
    ])
  )
  try {
    window.localStorage.setItem(MONITOR_HISTORY_CACHE_KEY, JSON.stringify(trimmed))
  } catch {
    const latestOnly = Object.fromEntries(
      Object.entries(trimmed).map(([key, points]) => [
        key,
        points.slice(-600),
      ])
    )
    try {
      window.localStorage.setItem(MONITOR_HISTORY_CACHE_KEY, JSON.stringify(latestOnly))
    } catch {
      window.localStorage.removeItem(MONITOR_HISTORY_CACHE_KEY)
    }
  }
}

const Metrics = () => {
  const location = useLocation()
  const navigate = useNavigate()
  const appTheme = useThemeStore((state) => state.mode)
  const {
    token: {
      colorBgContainer,
      colorBgElevated,
      colorBorderSecondary,
      colorFillSecondary,
      colorPrimaryBg,
      colorPrimaryBorder,
      colorText,
    },
  } = theme.useToken()
  const panelBorder = colorBorderSecondary
  const panelHeaderBg = colorFillSecondary
  const selectedPortBg = appTheme === 'dark' ? 'rgba(37, 99, 235, 0.24)' : colorPrimaryBg
  const selectedPortBorder = appTheme === 'dark' ? '#3b82f6' : colorPrimaryBorder
  const chartSurface = colorBgElevated || colorBgContainer
  const chartGrid = appTheme === 'dark' ? 'rgba(148, 163, 184, 0.28)' : '#dfe5ee'
  const chartAxis = appTheme === 'dark' ? 'rgba(226, 232, 240, 0.78)' : '#4b5563'
  const chartText = colorText
  const persistedState = (() => {
    if (typeof window === 'undefined') return null
    try {
      const raw = window.localStorage.getItem(MONITOR_CENTER_STATE_KEY)
      return raw ? JSON.parse(raw) : null
    } catch {
      return null
    }
  })()

  const [deviceKeyword, setDeviceKeyword] = useState(persistedState?.deviceKeyword || '')
  const [deviceOptions, setDeviceOptions] = useState<MonitorDeviceSearchOption[]>([])
  const [selectedDevice, setSelectedDevice] = useState<MonitorDevice | null>(persistedState?.selectedDevice || null)
  const [interfaces, setInterfaces] = useState<MonitorInterface[]>(persistedState?.interfaces || [])
  const [portKeyword, setPortKeyword] = useState(persistedState?.portKeyword || '')
  const [selectedInterfaceIndex, setSelectedInterfaceIndex] = useState<number | null>(persistedState?.selectedInterfaceIndex ?? null)
  const [selectedTargets, setSelectedTargets] = useState<SelectedTarget[]>(persistedState?.selectedTargets || [])
  const [selectedMonitorKey, setSelectedMonitorKey] = useState<MonitorGroupKey>(persistedState?.selectedMonitorKey || 'traffic')
  const [monitorSearchKeyword, setMonitorSearchKeyword] = useState(persistedState?.monitorSearchKeyword || '')
  const [rangeValue, setRangeValue] = useState(isValidRangeValue(persistedState?.rangeValue) ? persistedState.rangeValue : '-10m')
  const [refreshValue, setRefreshValue] = useState(persistedState?.refreshValue || 10)
  const [historyMap, setHistoryMap] = useState<Record<string, ChartPoint[]>>(() => loadCachedHistoryMap())
  const [queueSeriesMap, setQueueSeriesMap] = useState<Record<string, MonitorDynamicSeries[]>>({})
  const [zoomRanges, setZoomRanges] = useState<Record<string, ZoomRange>>({})
  const [dragSelections, setDragSelections] = useState<Record<string, ZoomRange>>({})
  const [draggingTargetKey, setDraggingTargetKey] = useState<string | null>(null)
  const [loadingDevices, setLoadingDevices] = useState(false)
  const [loadingInterfaces, setLoadingInterfaces] = useState(false)
  const [loadingChart, setLoadingChart] = useState(false)
  const restoredTargetsLoadedRef = useRef(false)
  const historyMapRef = useRef<Record<string, ChartPoint[]>>(historyMap)
  const queueSeriesMapRef = useRef<Record<string, MonitorDynamicSeries[]>>({})
  const historyFetchedAtRef = useRef<Record<string, number>>({})
  const historyPendingRef = useRef<Partial<Record<string, Promise<readonly [string, string, ChartPoint[], MonitorDynamicSeries[]?]>>>>({})

  const selectedRange = useMemo(
    () => {
      const range = RANGE_OPTIONS.find((item) => item.value === rangeValue) || RANGE_OPTIONS[0]
      return { ...range, interval: getAdaptiveInterval(range.value), rateWindow: getAdaptiveRateWindow(range.value) }
    },
    [rangeValue]
  )
  const selectedMonitorGroup = useMemo(
    () => MONITOR_GROUPS.find((item) => item.key === selectedMonitorKey) || MONITOR_GROUPS[0],
    [selectedMonitorKey]
  )

  useEffect(() => {
    historyMapRef.current = historyMap
  }, [historyMap])

  useEffect(() => {
    queueSeriesMapRef.current = queueSeriesMap
  }, [queueSeriesMap])

  const preloadTargetsFromRoute = async (
    routeTargets: Array<{
      deviceId?: number
      deviceIp?: string
      deviceName?: string
      portName: string
      side?: string
    }>
  ) => {
    if (!routeTargets.length) {
      return
    }

    setLoadingInterfaces(true)
    try {
      const resolvedTargets = (
        await Promise.all(
          routeTargets.map(async (target) => {
            const response = target.deviceId
              ? await getMonitorDeviceInterfaces(target.deviceId)
              : target.deviceIp
                ? await getMonitorDeviceByIp(target.deviceIp).then((device) => getMonitorDeviceInterfaces(device.id))
                : null

            if (!response) {
              return null
            }

            const matchedInterface =
              response.interfaces.find((item) => item.name === target.portName) ||
              response.interfaces.find((item) => item.description === target.portName) ||
              response.interfaces.find((item) => item.alias === target.portName)

            if (!matchedInterface) {
              return null
            }

            return {
              key: targetKey(response.device.id, matchedInterface.index),
              device: response.device,
              interface: matchedInterface,
            } satisfies SelectedTarget
          })
        )
      ).filter((item): item is SelectedTarget => Boolean(item))

      if (!resolvedTargets.length) {
        message.warning('未找到该线路关联的可监控端口')
        return
      }

      const primaryTarget = resolvedTargets[0]
      const primaryInterfaces = await getMonitorDeviceInterfaces(primaryTarget.device.id)
      setSelectedDevice(primaryInterfaces.device)
      setDeviceKeyword(primaryInterfaces.device.ip_address)
      setInterfaces(primaryInterfaces.interfaces)
      setPortKeyword(primaryTarget.interface.name)
      setSelectedInterfaceIndex(primaryTarget.interface.index)
      setSelectedTargets(resolvedTargets)
      await loadHistoryForTargets(resolvedTargets)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '加载线路监控目标失败')
    } finally {
      setLoadingInterfaces(false)
      navigate(location.pathname, { replace: true, state: null })
    }
  }

  const filteredMonitorGroups = useMemo(() => {
    const keyword = monitorSearchKeyword.trim().toLowerCase()
    if (!keyword) return MONITOR_GROUPS
    return MONITOR_GROUPS.filter((item) => item.label.toLowerCase().includes(keyword))
  }, [monitorSearchKeyword])

  const filteredInterfaces = useMemo(() => {
    const keyword = portKeyword.trim().toLowerCase()
    const selectedName = interfaces.find((item) => item.index === selectedInterfaceIndex)?.name.toLowerCase()
    if (!keyword) return interfaces
    if (selectedName && keyword === selectedName) return interfaces
    return interfaces.filter((item) => item.name.toLowerCase().includes(keyword))
  }, [interfaces, portKeyword, selectedInterfaceIndex])

  const sortedInterfaces = useMemo(() => {
    return [...filteredInterfaces].sort((a, b) => {
      const aDown = a.oper_status === 'down' ? 1 : 0
      const bDown = b.oper_status === 'down' ? 1 : 0
      if (aDown !== bDown) return aDown - bDown
      return a.index - b.index
    })
  }, [filteredInterfaces])

  useEffect(() => {
    if (!deviceKeyword.trim()) {
      setDeviceOptions([])
      return
    }
    const timer = window.setTimeout(async () => {
      setLoadingDevices(true)
      try {
        const result = await searchMonitorDevices(deviceKeyword.trim())
        setDeviceOptions(result)
      } catch (error: any) {
        setDeviceOptions([])
        message.error(error?.response?.data?.detail || '设备搜索失败')
      } finally {
        setLoadingDevices(false)
      }
    }, DEVICE_SEARCH_DEBOUNCE_MS)
    return () => window.clearTimeout(timer)
  }, [deviceKeyword])

  useEffect(() => {
    const routeTargets = location.state?.circuitMonitorTargets
    if (Array.isArray(routeTargets) && routeTargets.length) {
      void preloadTargetsFromRoute(routeTargets)
    }
  }, [location.state])

  const loadInterfaces = async (deviceId: number) => {
    setLoadingInterfaces(true)
    try {
      const response = await getMonitorDeviceInterfaces(deviceId)
      setSelectedDevice(response.device)
      setInterfaces(response.interfaces)
      if (response.interfaces.length > 0 && selectedInterfaceIndex === null) {
        setSelectedInterfaceIndex(response.interfaces[0].index)
      }
    } catch (error: any) {
      setInterfaces([])
      message.error(error?.response?.data?.detail || '读取端口信息失败')
    } finally {
      setLoadingInterfaces(false)
    }
  }

  const loadHistoryForTargets = async (targets: SelectedTarget[], nextRange = rangeValue, options?: { silent?: boolean; force?: boolean }) => {
    if (!targets.length) {
      setHistoryMap({})
      return
    }
    const rangeConfig = RANGE_OPTIONS.find((item) => item.value === nextRange) || RANGE_OPTIONS[0]
    const interval = getAdaptiveInterval(rangeConfig.value)
    const now = Date.now()
    const cacheEntries = targets.map((target) => ({
      target,
      cacheKey: historyCacheKey(target.key, rangeConfig.value, interval, selectedMonitorKey),
      fallbackKey: target.key,
    }))
    const hasVisibleCache = cacheEntries.some(({ cacheKey, fallbackKey }) => {
      const cached = historyMapRef.current[cacheKey] || (!isQueueDetailGroup(selectedMonitorKey) ? historyMapRef.current[fallbackKey] : undefined) || []
      return trimChartPointsToRange(cached, rangeConfig.value, now).length > 0
    })
    if (!options?.silent && !hasVisibleCache) {
      setLoadingChart(true)
    }
    try {
      const results = await Promise.all(
        cacheEntries.map(async ({ target, cacheKey, fallbackKey }) => {
          const cached = historyMapRef.current[cacheKey]
          const recentlyFetched = now - (historyFetchedAtRef.current[cacheKey] || 0) < HISTORY_REQUEST_CACHE_MS
          if (!options?.force && cached?.length && recentlyFetched) {
            return [cacheKey, fallbackKey, cached] as const
          }

          if (!options?.force && historyPendingRef.current[cacheKey]) {
            return historyPendingRef.current[cacheKey]
          }

          const pending = isQueueDetailGroup(selectedMonitorKey)
            ? getMonitorInterfaceQueueHistory(target.device.id, target.interface.index, {
                group: selectedMonitorKey,
                range: rangeConfig.value,
                interval,
              }).then((history) => [cacheKey, fallbackKey, history.data.map(toChartPoint), history.series] as const)
            : getMonitorInterfaceHistory(target.device.id, target.interface.index, {
                range: rangeConfig.value,
                interval,
                group: selectedMonitorKey,
              }).then((history) => [cacheKey, fallbackKey, history.data.map(toChartPoint)] as const)

          historyPendingRef.current[cacheKey] = pending
          try {
            return await pending
          } finally {
            delete historyPendingRef.current[cacheKey]
          }
        })
      )
      setHistoryMap((prev) => {
        const next = { ...prev }
        const updatedAt = Date.now()
        const nextSeries = { ...queueSeriesMapRef.current }
        for (const [key, fallbackKey, points, series] of results) {
          const trimmed = trimChartPointsToRetention(points, updatedAt)
          next[key] = trimmed
          if (!isQueueDetailGroup(selectedMonitorKey)) {
            next[fallbackKey] = trimmed
          }
          if (series) {
            nextSeries[key] = series
          }
          historyFetchedAtRef.current[key] = updatedAt
        }
        if (isQueueDetailGroup(selectedMonitorKey)) {
          setQueueSeriesMap(nextSeries)
        }
        return next
      })
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '读取历史监控数据失败')
    } finally {
      if (!options?.silent) {
        setLoadingChart(false)
      }
    }
  }

  useEffect(() => {
    const routeTargets = location.state?.circuitMonitorTargets
    if (restoredTargetsLoadedRef.current || (Array.isArray(routeTargets) && routeTargets.length) || !selectedTargets.length) {
      return
    }

    restoredTargetsLoadedRef.current = true
    void (async () => {
      await loadHistoryForTargets(selectedTargets)
    })()
  }, [selectedTargets, location.state])

  const handleDeviceSelect = async (value: string) => {
    const nextDeviceOption = deviceOptions.find((item) => String(item.id) === value)
    if (!nextDeviceOption) return

    try {
      const verifiedDevice = await getMonitorDeviceByIp(nextDeviceOption.ip_address)
      setSelectedDevice(verifiedDevice)
      setDeviceKeyword(nextDeviceOption.ip_address)
      setPortKeyword('')
      setSelectedInterfaceIndex(null)
      await loadInterfaces(verifiedDevice.id)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '监控连通性验证失败，请检查设备配置')
    }
  }

  const handleAddTarget = async () => {
    if (!selectedDevice || selectedInterfaceIndex === null) {
      message.warning('请先选择设备和端口')
      return
    }
    const foundInterface = interfaces.find((item) => item.index === selectedInterfaceIndex)
    if (!foundInterface) {
      message.warning('未找到对应端口')
      return
    }
    const key = targetKey(selectedDevice.id, foundInterface.index)
    const nextTarget: SelectedTarget = { key, device: selectedDevice, interface: foundInterface }
    const nextTargets = selectedTargets.some((item) => item.key === key)
      ? selectedTargets
      : [...selectedTargets, nextTarget]
    setSelectedTargets(nextTargets)
    await loadHistoryForTargets(nextTargets)
  }

  const removeTarget = (key: string) => {
    const nextTargets = selectedTargets.filter((item) => item.key !== key)
    setSelectedTargets(nextTargets)
    setHistoryMap((prev) => {
      const next = { ...prev }
      Object.keys(next).forEach((historyKey) => {
        if (historyKey === key || historyKey.startsWith(`${key}|`)) {
          delete next[historyKey]
        }
      })
      return next
    })
    setZoomRanges((prev) => {
      const next = { ...prev }
      delete next[key]
      return next
    })
    setDragSelections((prev) => {
      const next = { ...prev }
      delete next[key]
      return next
    })
  }

  const handleChartMouseDown = (targetKeyValue: string, event: any) => {
    if (event?.activeLabel == null) {
      return
    }
    const timestamp = Number(event.activeLabel)
    setDragSelections((prev) => ({
      ...prev,
      [targetKeyValue]: {
        start: timestamp,
        end: timestamp,
      },
    }))
  }

  const handleChartMouseMove = (targetKeyValue: string, event: any) => {
    if (event?.activeLabel == null) {
      return
    }
    setDragSelections((prev) => {
      const current = prev[targetKeyValue]
      if (!current?.start) {
        return prev
      }
      return {
        ...prev,
        [targetKeyValue]: {
          start: current.start,
          end: Number(event.activeLabel),
        },
      }
    })
  }

  const handleChartMouseUp = (targetKeyValue: string) => {
    setDragSelections((prev) => {
      const current = prev[targetKeyValue]
      if (!current?.start || !current?.end || current.start === current.end) {
        const next = { ...prev }
        delete next[targetKeyValue]
        return next
      }

      const start = Math.min(current.start, current.end)
      const end = Math.max(current.start, current.end)
      const zoomRangeValue = getRangeValueForWindowMs(end - start)
      const zoomInterval = getAdaptiveInterval(zoomRangeValue)
      const target = selectedTargets.find((item) => item.key === targetKeyValue)
      if (target && !isQueueDetailGroup(selectedMonitorKey)) {
        const cacheKey = zoomHistoryCacheKey(target.key, start, end, zoomInterval, selectedMonitorKey)
        void getMonitorInterfaceHistory(target.device.id, target.interface.index, {
          range: zoomRangeValue,
          interval: zoomInterval,
          group: selectedMonitorKey,
          start_ts: start,
          end_ts: end,
        }).then((history) => {
          setHistoryMap((historyPrev) => ({
            ...historyPrev,
            [cacheKey]: history.data.map(toChartPoint),
          }))
          historyFetchedAtRef.current[cacheKey] = Date.now()
          setZoomRanges((previous) => ({
            ...previous,
            [targetKeyValue]: { start, end, rangeValue: zoomRangeValue, interval: zoomInterval },
          }))
        }).catch((error: any) => {
          message.error(error?.response?.data?.detail || '加载缩放区间数据失败')
        })
      } else {
        setZoomRanges((previous) => ({
          ...previous,
          [targetKeyValue]: { start, end, rangeValue: zoomRangeValue, interval: zoomInterval },
        }))
      }

      const next = { ...prev }
      delete next[targetKeyValue]
      return next
    })
  }

  const resetChartZoom = (targetKeyValue: string) => {
    setZoomRanges((prev) => {
      const next = { ...prev }
      delete next[targetKeyValue]
      return next
    })
    setDragSelections((prev) => {
      const next = { ...prev }
      delete next[targetKeyValue]
      return next
    })
  }

  const handleRangeChange = (nextRange: string) => {
    setRangeValue(nextRange)
    setZoomRanges({})
    setDragSelections({})
  }

  const moveTargetCard = (sourceKey: string, destinationKey: string) => {
    if (sourceKey === destinationKey) return
    setSelectedTargets((prev) => {
      const sourceIndex = prev.findIndex((item) => item.key === sourceKey)
      const destinationIndex = prev.findIndex((item) => item.key === destinationKey)
      if (sourceIndex < 0 || destinationIndex < 0) return prev

      const next = [...prev]
      const [moved] = next.splice(sourceIndex, 1)
      next.splice(destinationIndex, 0, moved)
      return next
    })
  }

  useEffect(() => {
    if (!selectedTargets.length) return
    loadHistoryForTargets(selectedTargets, rangeValue)
  }, [rangeValue])

  useEffect(() => {
    if (!selectedTargets.length) return
    setZoomRanges({})
    setDragSelections({})
    loadHistoryForTargets(selectedTargets, rangeValue)
  }, [selectedMonitorKey])

  useEffect(() => {
    if (!selectedTargets.length) return
    const timer = window.setInterval(() => {
      loadHistoryForTargets(selectedTargets, rangeValue, { silent: true })
    }, refreshValue * 1000)
    return () => window.clearInterval(timer)
  }, [selectedTargets, refreshValue, rangeValue, selectedMonitorKey])

  useEffect(() => {
    persistHistoryMap(historyMap)
  }, [historyMap])

  useEffect(() => {
    if (typeof window === 'undefined') return
    window.localStorage.setItem(
      MONITOR_CENTER_STATE_KEY,
      JSON.stringify({
        deviceKeyword,
        selectedDevice,
        interfaces,
        portKeyword,
        selectedInterfaceIndex,
        selectedTargets,
        selectedMonitorKey,
        monitorSearchKeyword,
        rangeValue,
        refreshValue,
      })
    )
  }, [
    deviceKeyword,
    selectedDevice,
    interfaces,
    portKeyword,
    selectedInterfaceIndex,
    selectedTargets,
    selectedMonitorKey,
    monitorSearchKeyword,
    rangeValue,
    refreshValue,
  ])

  const chartCards = useMemo(() => {
    const now = Date.now()
    const rangeStart = now - getRangeWindowMs(rangeValue)
    return selectedTargets.map((target) => {
      const zoomRange = zoomRanges[target.key]
      const effectiveRangeValue = zoomRange?.start && zoomRange?.end
        ? (zoomRange.rangeValue || getRangeValueForWindowMs(zoomRange.end - zoomRange.start))
        : rangeValue
      const effectiveInterval = zoomRange?.start && zoomRange?.end
        ? (zoomRange.interval || getAdaptiveInterval(effectiveRangeValue))
        : selectedRange.interval
      const currentHistoryKey = zoomRange?.start && zoomRange?.end && !isQueueDetailGroup(selectedMonitorKey)
        ? zoomHistoryCacheKey(target.key, zoomRange.start, zoomRange.end, effectiveInterval, selectedMonitorKey)
        : historyCacheKey(target.key, rangeValue, selectedRange.interval, selectedMonitorKey)
      const gapThresholdMs = getGapThresholdMs(effectiveRangeValue, effectiveInterval, refreshValue)
      const series = isQueueDetailGroup(selectedMonitorKey)
        ? (queueSeriesMap[currentHistoryKey] || [])
        : selectedMonitorGroup.series
      const baseHistory = historyMap[currentHistoryKey] || []
      const rawData = zoomRange?.start && zoomRange?.end
        ? baseHistory
        : trimChartPointsToRange(baseHistory, effectiveRangeValue, now)
      const zoomedData = zoomRange?.start && zoomRange?.end
        ? rawData.filter((point) => point.timestamp >= zoomRange.start! && point.timestamp <= zoomRange.end!)
        : rawData
      const xDomain: [number, number] = zoomRange?.start && zoomRange?.end
        ? [zoomRange.start, zoomRange.end]
        : [rangeStart, now]
      const data = resampleChartData(zoomedData, series, xDomain, effectiveInterval, gapThresholdMs, selectedMonitorGroup.unit)
      const pointCount = data.filter((point) => hasSeriesValue(point, series)).length
      const dotStride = getChartDotStride(effectiveRangeValue, pointCount)
      const values = series.flatMap((serie) =>
        data.map((item) => item[serie.key]).filter((value): value is number => typeof value === 'number')
      )
      const max = values.length ? Math.max(...values) : null
      const speedValues = data
        .map((item) => item.speed_bps)
        .filter((value): value is number => typeof value === 'number' && value > 0)
      const speedCap = selectedMonitorGroup.unit === 'bps'
        ? (speedValues.length ? Math.max(...speedValues) : target.interface.speed_bps)
        : null
      const yAxis = getLinearYAxisTicks(values, 5, speedCap)
      const latestPoint = [...data].reverse().find((point) => hasSeriesValue(point, series))
      const xTicks = getLinearXAxisTicks(xDomain, effectiveRangeValue)
      const axisMax = yAxis.domain[1]

      return {
        target,
        data,
        series,
        max,
        latestPoint,
        domain: yAxis.domain,
        yTicks: yAxis.ticks,
        scale: getDisplayScale(selectedMonitorGroup.unit, axisMax),
        zoomRange,
        effectiveRangeValue,
        effectiveInterval,
        xDomain,
        xTicks,
        dragSelection: dragSelections[target.key],
        pointCount,
        dotStride,
      }
    })
  }, [historyMap, queueSeriesMap, selectedTargets, selectedMonitorGroup, selectedMonitorKey, selectedRange.interval, refreshValue, rangeValue, zoomRanges, dragSelections])

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <div style={{ color: '#8c8c8c', fontSize: 13 }}>监控中心 / 端口联合查询</div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
        <Card title="选择交换机端口" bodyStyle={{ padding: 12 }}>
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            <Space.Compact style={{ width: '100%' }}>
              <AutoComplete
                value={deviceKeyword}
                options={deviceOptions.map((item) => ({
                  value: String(item.id),
                  label: `${item.ip_address} / ${item.name}${item.device_role ? ` / ${item.device_role}` : ''}`,
                }))}
                onSearch={setDeviceKeyword}
                onSelect={handleDeviceSelect}
                placeholder="精确搜索IP..."
                style={{ width: '42%' }}
                notFoundContent={loadingDevices ? <Spin size="small" /> : '未匹配到设备'}
              />
              <AutoComplete
                value={portKeyword}
                options={sortedInterfaces.map((item) => ({
                  value: String(item.index),
                  label: item.name,
                }))}
                onSearch={setPortKeyword}
                onSelect={(value) => {
                  setSelectedInterfaceIndex(Number(value))
                  const found = interfaces.find((item) => item.index === Number(value))
                  if (found) {
                    setPortKeyword(found.name)
                  }
                }}
                placeholder="模糊搜索端口..."
                style={{ width: '38%' }}
                disabled={!selectedDevice}
                notFoundContent={loadingInterfaces ? <Spin size="small" /> : '未匹配到端口'}
              />
              <Button type="primary" icon={<PlusOutlined />} style={{ width: '20%' }} onClick={handleAddTarget}>
                添加端口
              </Button>
            </Space.Compact>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <div style={{ border: `1px solid ${panelBorder}`, minHeight: 250, borderRadius: 6, overflow: 'hidden' }}>
                <div style={{ padding: '10px 12px', borderBottom: `1px solid ${panelBorder}`, background: panelHeaderBg }}>
                  <Tag color="cyan">可选端口</Tag>
                  {selectedDevice ? <Text type="secondary">{` ${selectedDevice.ip_address}`}</Text> : null}
                </div>
                <div style={{ maxHeight: 250, overflowY: 'auto', padding: 8 }}>
                  {loadingInterfaces ? (
                    <div style={{ textAlign: 'center', padding: '64px 0' }}><Spin /></div>
                  ) : sortedInterfaces.length === 0 ? (
                    <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无端口数据" />
                  ) : (
                    sortedInterfaces.map((item) => {
                      const selected = item.index === selectedInterfaceIndex
                      return (
                        <div
                          key={item.index}
                          onClick={() => {
                            setSelectedInterfaceIndex(item.index)
                            setPortKeyword(item.name)
                          }}
                          style={{
                            padding: '8px 10px',
                            borderRadius: 4,
                            cursor: 'pointer',
                            background: selected ? selectedPortBg : undefined,
                            border: selected ? `1px solid ${selectedPortBorder}` : '1px solid transparent',
                            marginBottom: 6,
                          }}
                        >
                          <Space size={8}>
                            <Text>{item.name}</Text>
                            {item.oper_status === 'down' ? <Tag color="default">down</Tag> : <Tag color="success">up</Tag>}
                          </Space>
                        </div>
                      )
                    })
                  )}
                </div>
              </div>

              <div style={{ border: `1px solid ${panelBorder}`, minHeight: 250, borderRadius: 6, overflow: 'hidden' }}>
                <div style={{ padding: '10px 12px', borderBottom: `1px solid ${panelBorder}`, background: panelHeaderBg }}>
                  已选端口
                </div>
                <div style={{ maxHeight: 250, overflowY: 'auto', padding: 8 }}>
                  {selectedTargets.length === 0 ? (
                    <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="未添加端口" />
                  ) : (
                    selectedTargets.map((target) => (
                      <div
                        key={target.key}
                        style={{
                          padding: '8px 10px',
                          borderBottom: `1px solid ${panelBorder}`,
                          display: 'flex',
                          justifyContent: 'space-between',
                          gap: 12,
                        }}
                      >
                        <Space size={8}>
                          <Text>{`${target.device.ip_address}:${target.interface.name}`}</Text>
                          {target.interface.oper_status === 'down' ? <Tag color="default">down</Tag> : <Tag color="success">up</Tag>}
                        </Space>
                        <Button type="link" danger size="small" onClick={() => removeTarget(target.key)}>
                          移除
                        </Button>
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>
          </Space>
        </Card>

        <Card title="监控项" bodyStyle={{ padding: 12 }}>
          <Space direction="vertical" size="middle" style={{ width: '100%' }}>
            <Space.Compact style={{ width: '100%' }}>
              <AutoComplete
                value={monitorSearchKeyword}
                options={[]}
                onSearch={setMonitorSearchKeyword}
                placeholder="搜索监控项..."
                style={{ width: '82%' }}
              />
              <Button style={{ width: '18%' }} onClick={() => setMonitorSearchKeyword('')}>
                清除
              </Button>
            </Space.Compact>

            <div style={{ border: `1px solid ${panelBorder}`, minHeight: 250, borderRadius: 6, padding: 12, overflowY: 'auto' }}>
              <Radio.Group value={selectedMonitorKey} onChange={(e) => setSelectedMonitorKey(e.target.value)}>
                <Space direction="vertical" size="middle">
                  {filteredMonitorGroups.map((item) => (
                    <Radio key={item.key} value={item.key}>
                      {item.label}
                    </Radio>
                  ))}
                </Space>
              </Radio.Group>
            </div>
          </Space>
        </Card>
      </div>

      <Card bodyStyle={{ padding: 12 }}>
        <Space>
          <Select
            value={rangeValue}
            onChange={handleRangeChange}
            options={RANGE_OPTIONS.map((item) => ({ value: item.value, label: item.label }))}
            style={{ width: 220 }}
          />
          <Select
            value={refreshValue}
            onChange={setRefreshValue}
            options={REFRESH_OPTIONS.map((item) => ({ value: item.value, label: item.label }))}
            style={{ width: 160 }}
          />
          <Button
            icon={<ReloadOutlined />}
            onClick={() => {
              if (selectedTargets.length) {
                loadHistoryForTargets(selectedTargets, rangeValue, { silent: true, force: true })
              }
            }}
          />
        </Space>
      </Card>

      {loadingChart && selectedTargets.length > 0 && !chartCards.some((card) => card.data.length > 0) ? (
        <Card><div style={{ textAlign: 'center', padding: '80px 0' }}><Spin /></div></Card>
      ) : chartCards.length === 0 ? (
        <Card><Empty description="请先添加一个或多个 IP + 端口" /></Card>
      ) : (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: chartCards.length === 1 ? 'minmax(0, 1fr)' : 'repeat(2, minmax(0, 1fr))',
            gap: 16,
            alignItems: 'start',
          }}
        >
          {chartCards.map(({ target, data, series, max, latestPoint, domain, yTicks, scale, zoomRange, effectiveRangeValue, effectiveInterval, xDomain, xTicks, dragSelection, dotStride }) => (
            <Card
              key={target.key}
              onDragOver={(event) => {
                event.preventDefault()
                event.dataTransfer.dropEffect = 'move'
              }}
              onDrop={(event) => {
                event.preventDefault()
                const sourceKey = event.dataTransfer.getData('text/plain') || draggingTargetKey
                if (sourceKey) {
                  moveTargetCard(sourceKey, target.key)
                }
                setDraggingTargetKey(null)
              }}
              style={{
                minWidth: 0,
                opacity: draggingTargetKey === target.key ? 0.72 : 1,
                outline: draggingTargetKey && draggingTargetKey !== target.key ? `1px dashed ${colorPrimaryBorder}` : undefined,
                outlineOffset: 2,
              }}
              title={
                <Space size={8}>
                  <Tooltip title="拖拽调整图表位置">
                    <HolderOutlined
                      draggable
                      onDragStart={(event) => {
                        setDraggingTargetKey(target.key)
                        event.dataTransfer.effectAllowed = 'move'
                        event.dataTransfer.setData('text/plain', target.key)
                      }}
                      onDragEnd={() => setDraggingTargetKey(null)}
                      style={{ color: chartAxis, cursor: 'grab' }}
                    />
                  </Tooltip>
                  <LineChartOutlined style={{ color: '#70d34f' }} />
                  <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {`${target.device.ip_address} ${target.interface.name} ${selectedMonitorGroup.label}`}
                  </span>
                </Space>
              }
              extra={
                <Space size="middle">
                  <Tag>{`单位 ${getMonitorGroupUnitLabel(selectedMonitorGroup, scale)}`}</Tag>
                  <Tag>X轴 时间</Tag>
                  <Tag>{`粒度 ${effectiveInterval}`}</Tag>
                  {selectedMonitorGroup.unit === 'bps' ? <Tag>{`速率 ${selectedRange.rateWindow}`}</Tag> : null}
                  {zoomRange?.start && zoomRange?.end ? (
                    <Button size="small" onClick={() => resetChartZoom(target.key)}>
                      重置缩放
                    </Button>
                  ) : null}
                </Space>
              }
            >
              {data.length === 0 ? (
                <div
                  style={{
                    height: 360,
                    background: chartSurface,
                    border: `1px solid ${panelBorder}`,
                    borderRadius: 4,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                  }}
                >
                  <Empty
                    image={Empty.PRESENTED_IMAGE_SIMPLE}
                    description={<span style={{ color: chartAxis }}>当前时间范围内暂无后台采集数据</span>}
                  />
                </div>
              ) : (
                <div
                  style={{
                    width: '100%',
                    height: 430,
                    userSelect: 'none',
                    background: chartSurface,
                    border: `1px solid ${panelBorder}`,
                    borderRadius: 4,
                    padding: '14px 16px 10px 10px',
                  }}
                >
                  <ResponsiveContainer width="100%" height={320}>
                    <LineChart
                      data={data}
                      margin={{ top: 8, right: 16, left: 4, bottom: 0 }}
                      onMouseDown={(event) => handleChartMouseDown(target.key, event)}
                      onMouseMove={(event) => handleChartMouseMove(target.key, event)}
                      onMouseUp={() => handleChartMouseUp(target.key)}
                      style={{ userSelect: 'none', cursor: 'crosshair' }}
                    >
                      <CartesianGrid stroke={chartGrid} horizontal vertical />
                      <XAxis
                        dataKey="timestamp"
                        type="number"
                        scale="time"
                        domain={xDomain}
                        ticks={xTicks}
                        interval={0}
                        minTickGap={getXAxisMinTickGap(effectiveRangeValue)}
                        tickFormatter={(value) => formatXAxisTick(Number(value), effectiveRangeValue, xDomain as [number, number])}
                        tick={{ fill: chartAxis, fontSize: 12 }}
                        axisLine={{ stroke: panelBorder }}
                        tickLine={{ stroke: panelBorder }}
                      />
                      <YAxis
                        width={92}
                        tick={{ fill: chartAxis, fontSize: 12 }}
                        tickMargin={8}
                        domain={domain as [number, number]}
                        ticks={yTicks}
                        tickFormatter={(value) =>
                          formatAxisTick(Number(value), selectedMonitorGroup.unit, scale, max)
                        }
                        axisLine={{ stroke: panelBorder }}
                        tickLine={{ stroke: panelBorder }}
                      />
                      <RechartsTooltip
                        contentStyle={{ background: chartSurface, border: `1px solid ${panelBorder}`, color: chartText }}
                        labelStyle={{ color: chartText }}
                        formatter={(value: number) =>
                          formatMetricValue(value, selectedMonitorGroup.unit, scale)
                        }
                        labelFormatter={(label) => `时间: ${dayjs(Number(label)).format('YYYY-MM-DD HH:mm:ss')}`}
                      />
                      {series.map((serie) => (
                        <Line
                          key={serie.key}
                          type="linear"
                          dataKey={serie.key}
                          stroke={serie.color}
                          strokeWidth={1.6}
                          isAnimationActive={false}
                          dot={dotStride ? renderSampledDot(dotStride) : false}
                          activeDot={{ r: 4 }}
                          name={serie.label}
                          connectNulls={selectedMonitorGroup.unit === 'bps'}
                        />
                      ))}
                      {dragSelection?.start && dragSelection?.end ? (
                        <ReferenceArea
                          x1={Math.min(dragSelection.start, dragSelection.end)}
                          x2={Math.max(dragSelection.start, dragSelection.end)}
                          y1={domain[0]}
                          y2={domain[1]}
                          ifOverflow="hidden"
                          strokeOpacity={0.3}
                          fill="#1677ff"
                          fillOpacity={0.15}
                        />
                      ) : null}
                    </LineChart>
                  </ResponsiveContainer>
                  <div style={{ borderTop: `1px solid ${panelBorder}`, marginTop: 8, paddingTop: 8 }}>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 100px 100px 100px', color: chartAxis, fontSize: 12, fontWeight: 600 }}>
                      <span>Name</span>
                      <span style={{ textAlign: 'right' }}>Last</span>
                      <span style={{ textAlign: 'right' }}>Min</span>
                      <span style={{ textAlign: 'right' }}>Max</span>
                    </div>
                    {series.map((serie) => {
                      const seriesValues = data.map((item) => item[serie.key]).filter((value): value is number => typeof value === 'number')
                      const seriesMin = seriesValues.length ? Math.min(...seriesValues) : null
                      const seriesMax = seriesValues.length ? Math.max(...seriesValues) : null
                      return (
                        <div
                          key={serie.key}
                          style={{ display: 'grid', gridTemplateColumns: '1fr 100px 100px 100px', color: chartText, fontSize: 12, lineHeight: '24px' }}
                        >
                          <span>
                            <span style={{ display: 'inline-block', width: 14, height: 4, background: serie.color, borderRadius: 2, marginRight: 8, verticalAlign: 'middle' }} />
                            {serie.label}
                          </span>
                          <span style={{ textAlign: 'right' }}>
                            {formatMetricValue(
                              typeof latestPoint?.[serie.key] === 'number' ? latestPoint[serie.key] as number : null,
                              selectedMonitorGroup.unit,
                              scale
                            )}
                          </span>
                          <span style={{ textAlign: 'right' }}>{formatMetricValue(seriesMin, selectedMonitorGroup.unit, scale)}</span>
                          <span style={{ textAlign: 'right' }}>{formatMetricValue(seriesMax, selectedMonitorGroup.unit, scale)}</span>
                        </div>
                      )
                    })}
                  </div>
                </div>
              )}
            </Card>
          ))}
        </div>
      )}
    </Space>
  )
}

export default Metrics
