import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Button, Card, DatePicker, Empty, Input, Select, Space, Spin, message } from 'antd'
import { CloseOutlined, HolderOutlined, LockOutlined, ReloadOutlined, SearchOutlined, UnlockOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceArea,
  ResponsiveContainer,
  Tooltip as RechartsTooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { getDatacenters, type Datacenter } from '../../api/devices'
import {
  getCircuitTrafficHistory,
  getTrafficSummaryHistory,
  type MonitorHistoryPoint,
  type MonitorInterface,
} from '../../api/metrics'
import { getCircuits, getCustomers, type Circuit, type Customer } from '../../api/resources'
import { useAuthStore } from '../../store/auth'

const { RangePicker } = DatePicker

type TrafficTarget = {
  deviceId: number
  deviceIp: string
  deviceName?: string
  portName: string
  side?: string
}

type LoadedTrafficTarget = TrafficTarget & {
  interface: MonitorInterface
  data: TrafficChartPoint[]
}

type TrafficChartPoint = {
  timestamp: number
  timeLabel: string
  in_bps?: number | null
  out_bps?: number | null
}

type ChartScale = {
  divisor: number
  suffix: string
}

type TrafficDashboardPrefs = {
  locked?: boolean
  defaultCircuitIds?: number[]
  visibleCircuitIds?: number[]
}

type CircuitTrafficCard = {
  circuit: Circuit
  targets: LoadedTrafficTarget[]
  aggregateData?: TrafficChartPoint[]
  targetCount?: number
  skippedTargetCount?: number
  cached?: boolean
  generatedAt?: string
  loading?: boolean
  error?: string
}

type CardQueryState = {
  rangeValue: string
  intervalValue: string
  customRange: [dayjs.Dayjs | null, dayjs.Dayjs | null] | null
}

type SummaryPresetType = 'internet-all' | 'private-all' | 'internet-datacenter'

type SummaryPreset = {
  key: string
  label: string
  lineType: 'internet' | 'private_line'
  summaryType: SummaryPresetType
  datacenterId?: number
}

const RANGE_OPTIONS = [
  { value: '-10m', label: '过去10分钟' },
  { value: '-1h', label: '过去1小时' },
  { value: '-6h', label: '过去6小时' },
  { value: '-24h', label: '过去24小时' },
  { value: '-7d', label: '过去7天' },
  { value: 'custom', label: '自定义时间' },
]

const INTERVAL_OPTIONS = [
  { value: '10s', label: '10秒' },
  { value: '30s', label: '30秒' },
  { value: '1m', label: '1分钟' },
  { value: '5m', label: '5分钟' },
  { value: '15m', label: '15分钟' },
  { value: '1h', label: '1小时' },
]

const getDefaultIntervalForRange = (range: string, customRange?: [dayjs.Dayjs | null, dayjs.Dayjs | null] | null) => {
  if (range === '-10m') return '10s'
  if (range === '-1h') return '30s'
  if (range === '-6h') return '1m'
  if (range === '-24h') return '5m'
  if (range === '-7d') return '1h'
  if (range === 'custom' && customRange?.[0] && customRange?.[1]) {
    const minutes = customRange[1].diff(customRange[0], 'minute')
    if (minutes <= 30) return '10s'
    if (minutes <= 180) return '30s'
    if (minutes <= 720) return '1m'
    if (minutes <= 2880) return '5m'
    if (minutes <= 10080) return '15m'
    return '1h'
  }
  return '1m'
}

const formatTrafficTargetText = (target: TrafficTarget) => {
  const location = [target.deviceName || target.deviceIp, target.portName].filter(Boolean).join(' / ')
  return `${target.side || '端口'}：${location}`
}

const getTrafficTargets = (record: Circuit): TrafficTarget[] => {
  const targets = [
    record.primary_device_id && record.primary_device_ip && record.primary_port_name
      ? {
          deviceId: record.primary_device_id,
          deviceIp: record.primary_device_ip,
          deviceName: record.primary_device_name,
          portName: record.primary_port_name,
          side: '主线',
        }
      : null,
    record.access_mode === 'dual' && record.secondary_device_id && record.secondary_device_ip && record.secondary_port_name
      ? {
          deviceId: record.secondary_device_id,
          deviceIp: record.secondary_device_ip,
          deviceName: record.secondary_device_name,
          portName: record.secondary_port_name,
          side: '备线',
        }
      : null,
    record.aggregation_monitor_device_id && record.aggregation_monitor_device_ip && record.aggregation_interface_name
      ? {
          deviceId: record.aggregation_monitor_device_id,
          deviceIp: record.aggregation_monitor_device_ip,
          deviceName: record.aggregation_monitor_device_name,
          portName: record.aggregation_interface_name,
          side: '聚合',
        }
      : null,
  ].filter(Boolean)

  return targets as TrafficTarget[]
}

const getTrafficPrefsKey = (userId?: number | string, username?: string) => `traffic-query-dashboard:${userId || username || 'anonymous'}`

const readTrafficPrefs = (key: string): TrafficDashboardPrefs => {
  try {
    const raw = window.localStorage.getItem(key)
    return raw ? JSON.parse(raw) : {}
  } catch {
    return {}
  }
}

const writeTrafficPrefs = (key: string, prefs: TrafficDashboardPrefs) => {
  try {
    window.localStorage.setItem(key, JSON.stringify(prefs))
  } catch {
    // localStorage may be unavailable in private mode; ignore without affecting query.
  }
}

const formatBps = (value?: number | null) => {
  const safe = Math.abs(value || 0)
  if (safe >= 1_000_000_000) return `${(Number(value || 0) / 1_000_000_000).toFixed(2)} Gbps`
  if (safe >= 1_000_000) return `${(Number(value || 0) / 1_000_000).toFixed(2)} Mbps`
  if (safe >= 1_000) return `${(Number(value || 0) / 1_000).toFixed(2)} Kbps`
  return `${(value || 0).toFixed(0)} bps`
}

const normalizeChartData = (data: TrafficChartPoint[]) => {
  return data
    .filter((point) => Number.isFinite(point.timestamp))
    .map((point) => ({
      ...point,
      in_bps: Number.isFinite(Number(point.in_bps)) ? Number(point.in_bps) : 0,
      out_bps: Number.isFinite(Number(point.out_bps)) ? Number(point.out_bps) : 0,
    }))
    .sort((a, b) => a.timestamp - b.timestamp)
}

const getChartScale = (data: TrafficChartPoint[]) => {
  const maxValue = Math.max(
    0,
    ...data.flatMap((point) => [Math.abs(Number(point.in_bps || 0)), Math.abs(Number(point.out_bps || 0))])
  )
  if (maxValue >= 1_000_000_000) return { divisor: 1_000_000_000, suffix: 'Gbps' }
  if (maxValue >= 1_000_000) return { divisor: 1_000_000, suffix: 'Mbps' }
  if (maxValue >= 1_000) return { divisor: 1_000, suffix: 'Kbps' }
  return { divisor: 1, suffix: 'bps' }
}

const formatScaledAxisValue = (value: number, scale: ChartScale) => {
  const scaled = Number(value) / scale.divisor
  const abs = Math.abs(scaled)
  const formatted = scaled.toFixed(abs >= 100 ? 0 : abs >= 10 ? 1 : 2).replace(/\.0+$/, '').replace(/(\.\d*?)0+$/, '$1')
  return `${formatted}${scale.suffix}`
}

const formatTimeAxisValue = (value: number, data: TrafficChartPoint[]) => {
  const first = data[0]?.timestamp
  const last = data[data.length - 1]?.timestamp
  const span = Number.isFinite(first) && Number.isFinite(last) ? Math.abs(last - first) : 0
  if (span <= 60 * 60 * 1000) return dayjs(value).format('MM-DD HH:mm:ss')
  if (span <= 24 * 60 * 60 * 1000) return dayjs(value).format('MM-DD HH:mm')
  return dayjs(value).format('MM-DD HH:mm')
}

const toChartPoint = (point: MonitorHistoryPoint): TrafficChartPoint | null => {
  const rawTime = point._time || point.time
  const timestamp = rawTime ? dayjs(rawTime).valueOf() : NaN
  if (!Number.isFinite(timestamp)) return null
  return {
    timestamp,
    timeLabel: dayjs(timestamp).format('MM-DD HH:mm'),
    in_bps: point.in_bps ?? null,
    out_bps: point.out_bps ?? null,
  }
}

const mergeAggregateData = (targets: LoadedTrafficTarget[]): TrafficChartPoint[] => {
  const byTime = new Map<number, TrafficChartPoint>()
  targets.forEach((target) => {
    target.data.forEach((point) => {
      const current = byTime.get(point.timestamp) || { timestamp: point.timestamp, timeLabel: point.timeLabel, in_bps: 0, out_bps: 0 }
      current.in_bps = Number(current.in_bps || 0) + Number(point.in_bps || 0)
      current.out_bps = Number(current.out_bps || 0) + Number(point.out_bps || 0)
      byTime.set(point.timestamp, current)
    })
  })
  return Array.from(byTime.values()).sort((a, b) => a.timestamp - b.timestamp)
}

const getStableNegativeId = (key: string) => {
  let hash = 0
  key.split('').forEach((char) => {
    hash = ((hash * 31) + char.charCodeAt(0)) >>> 0
  })
  return -Number(hash || 900)
}

const buildSummaryCircuit = (preset: SummaryPreset): Circuit => ({
  id: getStableNegativeId(`summary:${preset.key}`),
  name: preset.label,
  datacenter_id: preset.datacenterId,
  datacenter_name: undefined,
  line_type: preset.lineType,
  access_mode: 'single',
  status: 'active',
} as Circuit)

const DEFAULT_SUMMARY_PRESETS: SummaryPreset[] = [
  { key: 'internet-all', label: '公网流量汇总', lineType: 'internet', summaryType: 'internet-all' },
  { key: 'private-all', label: '专线流量汇总', lineType: 'private_line', summaryType: 'private-all' },
]

const getSummaryCardSubtitle = (preset: SummaryPreset) => {
  if (preset.summaryType === 'internet-datacenter') {
    return '该机房已启用公网线路上下行汇总'
  }
  return preset.lineType === 'internet' ? '全部已启用公网线路上下行汇总' : '全部已启用专线线路上下行汇总'
}

const DEFAULT_CARD_QUERY_STATE: CardQueryState = {
  rangeValue: '-24h',
  intervalValue: '5m',
  customRange: null,
}

const getSummaryPresetId = (preset: SummaryPreset) => buildSummaryCircuit(preset).id

const buildSummaryPresets = (datacenters: Datacenter[]): SummaryPreset[] => {
  const datacenterPresetMap = new Map<string, SummaryPreset>()
  datacenters.forEach((item) => {
    const label = `${item.name}公网流量`
    if (datacenterPresetMap.has(label)) return
    datacenterPresetMap.set(label, {
      key: `internet-dc-${item.id}`,
      label,
      lineType: 'internet' as const,
      summaryType: 'internet-datacenter' as const,
      datacenterId: item.id,
    })
  })
  const datacenterPresets = Array.from(datacenterPresetMap.values())
  return [...DEFAULT_SUMMARY_PRESETS, ...datacenterPresets]
}

const getCardTitle = (card: CircuitTrafficCard, target?: LoadedTrafficTarget) => {
  if (!target) return card.circuit.name || '流量汇总'
  const side = target.side ? `${target.side} · ` : ''
  return `${card.circuit.name} · ${side}${target.deviceName || target.deviceIp} / ${target.portName}`
}

const getCardSubtitle = (card: CircuitTrafficCard, target?: LoadedTrafficTarget) => {
  if (target) return `${target.deviceName || target.deviceIp} / ${target.portName}`
  return getTrafficTargets(card.circuit).map(formatTrafficTargetText).join('  |  ')
}

const TrafficChart = ({
  title,
  subtitle,
  data,
  scale,
}: {
  title?: string
  subtitle?: string
  data: TrafficChartPoint[]
  scale: ChartScale
}) => {
  const [left, setLeft] = useState<number | null>(null)
  const [right, setRight] = useState<number | null>(null)
  const [domain, setDomain] = useState<[number | 'dataMin', number | 'dataMax']>(['dataMin', 'dataMax'])
  const chartData = useMemo(() => normalizeChartData(data), [data])

  useEffect(() => {
    setDomain(['dataMin', 'dataMax'])
    setLeft(null)
    setRight(null)
  }, [data])

  const areaData = chartData.map((point) => ({
    ...point,
    in_area_bps: Math.max(Number(point.in_bps || 0), 0),
    out_line_bps: Math.max(Number(point.out_bps || 0), 0),
  }))

  const zoom = () => {
    if (left === null || right === null || left === right) {
      setLeft(null)
      setRight(null)
      return
    }
    setDomain(left < right ? [left, right] : [right, left])
    setLeft(null)
    setRight(null)
  }

  const resetZoom = () => {
    setDomain(['dataMin', 'dataMax'])
    setLeft(null)
    setRight(null)
  }

  return (
    <Card
      size="small"
      title={(
        <div style={{ minWidth: 0 }}>
          <div style={{ fontWeight: 600, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={title}>
            {title || '流量曲线'}
          </div>
          {subtitle ? (
            <div
              style={{ marginTop: 2, fontSize: 12, color: '#8c8c8c', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}
              title={subtitle}
            >
              {subtitle}
            </div>
          ) : null}
        </div>
      )}
      extra={domain[0] !== 'dataMin' ? <Button size="small" type="link" onClick={resetZoom}>还原</Button> : null}
      styles={{ body: { height: 300 } }}
    >
      {chartData.length ? (
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart
            data={areaData}
            margin={{ top: 12, right: 28, bottom: 28, left: 8 }}
            onMouseDown={(event) => setLeft(Number(event?.activeLabel) || null)}
            onMouseMove={(event) => left !== null ? setRight(Number(event?.activeLabel) || null) : undefined}
            onMouseUp={zoom}
          >
            <CartesianGrid strokeDasharray="3 3" stroke="#d9f7be" />
            <XAxis
              dataKey="timestamp"
              type="number"
              domain={domain}
              allowDataOverflow
              tickFormatter={(value) => formatTimeAxisValue(Number(value), chartData)}
              minTickGap={36}
              tick={{ fontSize: 12 }}
            />
            <YAxis tickFormatter={(value) => formatScaledAxisValue(Number(value), scale)} width={82} tick={{ fontSize: 12 }} />
            <RechartsTooltip
              labelFormatter={(value) => dayjs(Number(value)).format('YYYY-MM-DD HH:mm:ss')}
              formatter={(value: any, name: string) => [formatBps(Number(value)), name === 'in_area_bps' ? '入方向' : '出方向']}
            />
            <Area type="monotone" dataKey="in_area_bps" name="入方向" stroke="#35a800" fill="#35a800" fillOpacity={0.82} strokeWidth={1.2} dot={false} connectNulls={false} />
            <Area type="monotone" dataKey="out_line_bps" name="出方向" stroke="#5f7fd8" fill="transparent" strokeWidth={1.5} dot={false} connectNulls={false} />
            {left !== null && right !== null ? <ReferenceArea x1={left} x2={right} strokeOpacity={0.2} fill="#1677ff" fillOpacity={0.12} /> : null}
          </AreaChart>
        </ResponsiveContainer>
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前时间范围暂无流量数据" />
      )}
    </Card>
  )
}

const TrafficQuery = () => {
  const currentUser = useAuthStore((state) => state.user)
  const [optionsLoading, setOptionsLoading] = useState(false)
  const [dashboardLoading, setDashboardLoading] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [selectedLineId, setSelectedLineId] = useState<number | undefined>()
  const [lineType, setLineType] = useState<'internet' | 'private_line'>('internet')
  const [datacenterId, setDatacenterId] = useState<number | undefined>()
  const [providerKey, setProviderKey] = useState<string | undefined>()
  const [customerId, setCustomerId] = useState<number | undefined>()
  const [datacenters, setDatacenters] = useState<Datacenter[]>([])
  const [customers, setCustomers] = useState<Customer[]>([])
  const [filterOptionItems, setFilterOptionItems] = useState<Circuit[]>([])
  const [dashboardCards, setDashboardCards] = useState<Record<number, CircuitTrafficCard>>({})
  const [cardQueryStates, setCardQueryStates] = useState<Record<number, CardQueryState>>({})
  const [visibleCircuitIds, setVisibleCircuitIds] = useState<number[]>([])
  const [selectedPresetKeys, setSelectedPresetKeys] = useState<string[]>(DEFAULT_SUMMARY_PRESETS.map((item) => item.key))
  const [layoutLocked, setLayoutLocked] = useState(false)
  const [draggingId, setDraggingId] = useState<number | null>(null)
  const trafficRequestSeqRef = useRef<Record<number, number>>({})

  const prefsKey = useMemo(() => getTrafficPrefsKey(currentUser?.id, currentUser?.username), [currentUser?.id, currentUser?.username])
  const summaryPresets = useMemo(() => buildSummaryPresets(datacenters), [datacenters])

  const loadOptions = async () => {
    setOptionsLoading(true)
    try {
      const [datacenterResult, customerResult] = await Promise.all([
        getDatacenters(),
        getCustomers(),
      ])
      setDatacenters((datacenterResult || []).filter((item) => item.is_active !== false))
      setCustomers((customerResult.items || []).filter((item) => item.is_active !== false))
    } catch (error) {
      message.warning('资源筛选项加载失败，仍可使用关键字查询')
    } finally {
      setOptionsLoading(false)
    }
  }

  const loadFilterOptionItems = useCallback(async () => {
    try {
      const result = await getCircuits({
        limit: 1000,
        line_type: lineType,
        status: 'active',
        datacenter_id: datacenterId,
      })
      setFilterOptionItems(result.items || [])
    } catch {
      setFilterOptionItems([])
    }
  }, [datacenterId, lineType])

  const savePrefs = useCallback((patch: TrafficDashboardPrefs) => {
    const next = { ...readTrafficPrefs(prefsKey), ...patch }
    writeTrafficPrefs(prefsKey, next)
  }, [prefsKey])

  const getCardQueryState = useCallback((cardId: number): CardQueryState => {
    return cardQueryStates[cardId] || DEFAULT_CARD_QUERY_STATE
  }, [cardQueryStates])

  const patchCardQueryState = useCallback((cardId: number, patch: Partial<CardQueryState>) => {
    setCardQueryStates((prev) => {
      const current = prev[cardId] || DEFAULT_CARD_QUERY_STATE
      return {
        ...prev,
        [cardId]: {
          ...current,
          ...patch,
        },
      }
    })
  }, [])

  const loadSummaryTrafficCard = useCallback(async (preset: SummaryPreset, force = false, queryOverride?: CardQueryState) => {
    const summaryCircuit = buildSummaryCircuit(preset)
    const query = queryOverride || getCardQueryState(summaryCircuit.id)
    const requestSeq = (trafficRequestSeqRef.current[summaryCircuit.id] || 0) + 1
    trafficRequestSeqRef.current[summaryCircuit.id] = requestSeq
    setDashboardCards((prev) => ({
      ...prev,
      [summaryCircuit.id]: {
        circuit: summaryCircuit,
        targets: prev[summaryCircuit.id]?.targets || [],
        aggregateData: prev[summaryCircuit.id]?.aggregateData || [],
        targetCount: prev[summaryCircuit.id]?.targetCount,
        skippedTargetCount: prev[summaryCircuit.id]?.skippedTargetCount,
        loading: true,
      },
    }))
    try {
      const history = await getTrafficSummaryHistory({
        line_type: preset.lineType,
        datacenter_id: preset.datacenterId,
        range: query.rangeValue === 'custom' ? '-1h' : query.rangeValue,
        interval: query.intervalValue,
        ...(query.rangeValue === 'custom' && query.customRange?.[0] && query.customRange?.[1]
          ? { start_ts: query.customRange[0].valueOf(), end_ts: query.customRange[1].valueOf() }
          : {}),
        fresh: force,
      })
      const aggregateData = (history.data || []).map(toChartPoint).filter(Boolean) as TrafficChartPoint[]
      if (trafficRequestSeqRef.current[summaryCircuit.id] !== requestSeq) return
      setDashboardCards((prev) => ({
        ...prev,
        [summaryCircuit.id]: {
          circuit: summaryCircuit,
          targets: [],
          aggregateData,
          targetCount: history.target_count,
          skippedTargetCount: history.skipped_target_count,
          cached: history.cached,
          generatedAt: history.generated_at,
          loading: false,
        },
      }))
    } catch (error: any) {
      if (trafficRequestSeqRef.current[summaryCircuit.id] !== requestSeq) return
      const errorText = error?.message || error?.response?.data?.detail || '加载汇总流量失败'
      setDashboardCards((prev) => ({
        ...prev,
        [summaryCircuit.id]: { circuit: summaryCircuit, targets: [], aggregateData: [], loading: false, error: errorText },
      }))
    }
  }, [getCardQueryState])

  const loadTrafficCard = useCallback(async (record: Circuit, silent = false, force = false, queryOverride?: CardQueryState) => {
    const query = queryOverride || getCardQueryState(record.id)
    const requestSeq = (trafficRequestSeqRef.current[record.id] || 0) + 1
    trafficRequestSeqRef.current[record.id] = requestSeq
    setDashboardCards((prev) => ({
      ...prev,
      [record.id]: {
        circuit: record,
        targets: prev[record.id]?.targets || [],
        aggregateData: prev[record.id]?.aggregateData || [],
        loading: true,
      },
    }))
    try {
      if (query.rangeValue === 'custom' && (!query.customRange?.[0] || !query.customRange?.[1])) {
        throw new Error('请选择自定义开始和结束时间')
      }
      const history = await getCircuitTrafficHistory(record.id, {
        range: query.rangeValue === 'custom' ? '-1h' : query.rangeValue,
        interval: query.intervalValue,
        ...(query.rangeValue === 'custom' && query.customRange?.[0] && query.customRange?.[1]
          ? { start_ts: query.customRange[0].valueOf(), end_ts: query.customRange[1].valueOf() }
          : {}),
        fresh: force,
      })
      const targets = (history.targets || []).map((target) => ({
        deviceId: target.device_id,
        deviceIp: target.device_ip || '',
        deviceName: target.device_name,
        portName: target.port_name || target.interface?.name || '',
        side: target.side,
        interface: target.interface,
        data: (target.data || []).map(toChartPoint).filter(Boolean) as TrafficChartPoint[],
      }))
      const aggregateData = ((history.aggregate || history.data || [])).map(toChartPoint).filter(Boolean) as TrafficChartPoint[]
      if (trafficRequestSeqRef.current[record.id] !== requestSeq) return
      setDashboardCards((prev) => ({
        ...prev,
        [record.id]: {
          circuit: record,
          targets,
          aggregateData,
          targetCount: history.target_count,
          skippedTargetCount: history.skipped_target_count,
          cached: history.cached,
          generatedAt: history.generated_at,
          loading: false,
        },
      }))
    } catch (error: any) {
      if (trafficRequestSeqRef.current[record.id] !== requestSeq) return
      const errorText = error?.message || error?.response?.data?.detail || '加载流量曲线失败'
      setDashboardCards((prev) => ({ ...prev, [record.id]: { circuit: record, targets: [], aggregateData: [], loading: false, error: errorText } }))
      if (!silent) message.error(errorText)
    }
  }, [getCardQueryState])

  const ensureTrafficCard = useCallback(async (record: Circuit, options?: { makeVisible?: boolean; silent?: boolean }) => {
    if (options?.makeVisible !== false) {
      setVisibleCircuitIds((prev) => {
        if (prev.includes(record.id)) return prev
        const next = [...prev, record.id]
        savePrefs({ visibleCircuitIds: next })
        return next
      })
    }
    await loadTrafficCard(record, options?.silent)
  }, [loadTrafficCard, savePrefs])

  const loadDefaultDashboard = useCallback(async () => {
    const prefs = readTrafficPrefs(prefsKey)
    const defaults = (prefs.defaultCircuitIds || []).filter(Boolean)
    const visible = (prefs.visibleCircuitIds || defaults).filter(Boolean)
    setVisibleCircuitIds(visible)
    setLayoutLocked(Boolean(prefs.locked))

    setDashboardLoading(true)
    try {
      const defaultPresetKeys = DEFAULT_SUMMARY_PRESETS.map((item) => item.key)
      setSelectedPresetKeys(defaultPresetKeys)
      await Promise.all(DEFAULT_SUMMARY_PRESETS.map((preset) => loadSummaryTrafficCard(preset)))
      if (visible.length) {
        const result = await getCircuits({ limit: 1000, status: 'active' })
        const byId = new Map((result.items || []).map((item) => [item.id, item]))
        const circuits = visible.map((id) => byId.get(id)).filter(Boolean) as Circuit[]
        await Promise.all(circuits.map((record) => ensureTrafficCard(record, { makeVisible: false, silent: true })))
      }
    } finally {
      setDashboardLoading(false)
    }
  }, [ensureTrafficCard, loadSummaryTrafficCard, prefsKey])

  useEffect(() => {
    void loadOptions()
  }, [])

  useEffect(() => {
    void loadDefaultDashboard()
    // 首次进入页面只初始化默认图表；时间范围变化由下面的轻量刷新逻辑处理，避免重复跑完整初始化。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefsKey])

  useEffect(() => {
    void loadFilterOptionItems()
  }, [loadFilterOptionItems])

  useEffect(() => {
    setProviderKey(undefined)
    setCustomerId(undefined)
    setSelectedLineId(undefined)
  }, [datacenterId])

  useEffect(() => {
    setProviderKey(undefined)
    setCustomerId(undefined)
    setSelectedLineId(undefined)
  }, [lineType])

  const providerOptions = useMemo(() => {
    const optionMap = new Map<string, { label: string; value: string }>()
    filterOptionItems.forEach((item) => {
      const name = item.vendor_name || item.operator_name
      if (name) optionMap.set(`provider:${name}`, { label: name, value: `provider:${name}` })
    })
    return Array.from(optionMap.values()).sort((a, b) => a.label.localeCompare(b.label, 'zh-CN'))
  }, [filterOptionItems])

  const customerOptions = useMemo(() => {
    const optionMap = new Map<number, { label: string; value: number }>()
    filterOptionItems.forEach((item) => {
      if (item.customer_id && item.customer_name) optionMap.set(item.customer_id, { label: item.customer_name, value: item.customer_id })
    })
    if (!optionMap.size && !datacenterId) {
      customers.forEach((item) => optionMap.set(item.id, { label: item.name, value: item.id }))
    }
    return Array.from(optionMap.values()).sort((a, b) => a.label.localeCompare(b.label, 'zh-CN'))
  }, [customers, datacenterId, filterOptionItems])

  const lineOptions = useMemo(() => {
    const providerName = providerKey?.split(':').slice(1).join(':')
    const text = keyword.trim().toLowerCase()
    return filterOptionItems
      .filter((item) => {
        if (lineType === 'internet' && providerName) {
          const itemProvider = item.vendor_name || item.operator_name || ''
          if (itemProvider !== providerName) return false
        }
        if (lineType === 'private_line' && customerId && item.customer_id !== customerId) return false
        if (!text) return true
        return [
          item.name,
          item.operator_name,
          item.vendor_name,
          item.datacenter_name,
          item.customer_name,
          item.primary_device_name,
          item.primary_device_ip,
          item.primary_port_name,
          item.secondary_device_name,
          item.secondary_device_ip,
          item.secondary_port_name,
          item.aggregation_monitor_device_name,
          item.aggregation_monitor_device_ip,
          item.aggregation_interface_name,
        ].some((value) => String(value || '').toLowerCase().includes(text))
      })
      .map((item) => {
        const positionText = getTrafficTargets(item).map(formatTrafficTargetText).join('  |  ') || '未绑定监控交换机端口'
        return {
          labelText: item.name,
          label: (
            <div style={{ lineHeight: 1.35, padding: '2px 0', minWidth: 0 }}>
              <div style={{ fontWeight: 500, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={item.name}>{item.name}</div>
              <div style={{ fontSize: 12, color: '#8c8c8c', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={positionText}>{positionText}</div>
            </div>
          ),
          value: item.id,
          circuit: item,
          searchText: `${item.name} ${positionText} ${item.datacenter_name || ''} ${item.operator_name || ''} ${item.vendor_name || ''} ${item.customer_name || ''}`,
        }
      })
      .sort((a, b) => a.labelText.localeCompare(b.labelText, 'zh-CN'))
  }, [customerId, filterOptionItems, keyword, lineType, providerKey])

  const handleSelectLine = (id?: number) => {
    setSelectedLineId(id)
    if (!id) return
    const option = lineOptions.find((item) => item.value === id)
    if (option?.circuit) {
      patchCardQueryState(option.circuit.id, getCardQueryState(option.circuit.id))
      void ensureTrafficCard(option.circuit, { makeVisible: true })
    }
  }

  const handleCardRangeChange = (cardId: number, value: string) => {
    const current = getCardQueryState(cardId)
    const nextQuery = {
      ...current,
      rangeValue: value,
      intervalValue: getDefaultIntervalForRange(value, current.customRange),
    }
    patchCardQueryState(cardId, nextQuery)
    if (value !== 'custom') {
      void reloadCardById(cardId, false, nextQuery)
    }
  }

  const handleCardCustomRangeChange = (cardId: number, value: [dayjs.Dayjs | null, dayjs.Dayjs | null] | null) => {
    const current = getCardQueryState(cardId)
    const nextInterval = current.rangeValue === 'custom' ? getDefaultIntervalForRange('custom', value) : current.intervalValue
    const nextQuery = {
      ...current,
      customRange: value,
      intervalValue: nextInterval,
    }
    patchCardQueryState(cardId, nextQuery)
    if (current.rangeValue === 'custom' && value?.[0] && value?.[1]) {
      void reloadCardById(cardId, false, nextQuery)
    }
  }

  const handleCardIntervalChange = (cardId: number, value: string) => {
    const current = getCardQueryState(cardId)
    const nextQuery = { ...current, intervalValue: value }
    patchCardQueryState(cardId, nextQuery)
    void reloadCardById(cardId, false, nextQuery)
  }

  const handleSelectPresets = async (keys: string[]) => {
    const defaultKeys = DEFAULT_SUMMARY_PRESETS.map((item) => item.key)
    const extraKeys = keys.filter((key) => !defaultKeys.includes(key))
    const nextKeys = Array.from(new Set([...defaultKeys, ...extraKeys]))
    const prevKeys = selectedPresetKeys
    setSelectedPresetKeys(nextKeys)

    const addedKeys = nextKeys.filter((key) => !prevKeys.includes(key))
    const removedKeys = prevKeys.filter((key) => !nextKeys.includes(key))

    if (removedKeys.length) {
      setDashboardCards((prev) => {
        const next = { ...prev }
        removedKeys.forEach((key) => {
          const preset = summaryPresets.find((item) => item.key === key)
          if (preset) delete next[getSummaryPresetId(preset)]
        })
        return next
      })
    }

    if (!addedKeys.length) return

    setDashboardLoading(true)
    try {
      const presets = summaryPresets.filter((item) => addedKeys.includes(item.key))
      await Promise.all(presets.map((preset) => loadSummaryTrafficCard(preset)))
    } finally {
      setDashboardLoading(false)
    }
  }

  const resetFilters = () => {
    setKeyword('')
    setSelectedLineId(undefined)
    setDatacenterId(undefined)
    setProviderKey(undefined)
    setCustomerId(undefined)
  }

  const refreshVisibleCards = async () => {
    const cards = visibleCircuitIds.map((id) => dashboardCards[id]).filter(Boolean)
    const activePresets = summaryPresets.filter((preset) => selectedPresetKeys.includes(preset.key))
    await Promise.all([
      ...activePresets.map((preset) => loadSummaryTrafficCard(preset, true)),
      ...cards.map((card) => loadTrafficCard(card.circuit, true, true)),
    ])
  }

  const removeCard = (id: number) => {
    setVisibleCircuitIds((prev) => {
      const next = prev.filter((item) => item !== id)
      savePrefs({ visibleCircuitIds: next })
      return next
    })
    setDashboardCards((prev) => {
      const next = { ...prev }
      delete next[id]
      return next
    })
  }

  const reorderVisibleCards = (sourceId: number, targetId: number) => {
    if (layoutLocked || sourceId === targetId) return
    setVisibleCircuitIds((prev) => {
      const sourceIndex = prev.indexOf(sourceId)
      const targetIndex = prev.indexOf(targetId)
      if (sourceIndex < 0 || targetIndex < 0) return prev
      const next = [...prev]
      const [moved] = next.splice(sourceIndex, 1)
      next.splice(targetIndex, 0, moved)
      savePrefs({ visibleCircuitIds: next })
      return next
    })
  }

  const toggleLayoutLocked = () => {
    setLayoutLocked((prev) => {
      const next = !prev
      savePrefs({ locked: next })
      return next
    })
  }

  const visibleCards = Array.from(new Set(visibleCircuitIds)).map((id) => dashboardCards[id]).filter(Boolean)
  const summaryCards = summaryPresets
    .filter((preset) => selectedPresetKeys.includes(preset.key))
    .map((preset) => dashboardCards[getSummaryPresetId(preset)])
    .filter(Boolean)
    .filter((card, index, cards) => cards.findIndex((item) => item.circuit.id === card.circuit.id || item.circuit.name === card.circuit.name) === index)
  const summaryCardNames = new Set(summaryCards.map((card) => card.circuit.name))
  const visibleNonDuplicateCards = visibleCards.filter((card) => !summaryCardNames.has(card.circuit.name))

  const reloadCardById = async (cardId: number, force = false, queryOverride?: CardQueryState) => {
    const card = dashboardCards[cardId]
    if (!card) return
    if (card.circuit.id < 0) {
      const preset = summaryPresets.find((item) => getSummaryPresetId(item) === card.circuit.id)
      if (preset) await loadSummaryTrafficCard(preset, force, queryOverride)
      return
    }
    await loadTrafficCard(card.circuit, true, force, queryOverride)
  }

  const renderTrafficCard = (card: CircuitTrafficCard) => {
    const aggregateData = card.aggregateData || mergeAggregateData(card.targets)
    const chartScale = getChartScale(aggregateData)
    const isSummaryCard = card.circuit.id < 0
    const showTargetCharts = !isSummaryCard && card.targets.length > 1
    const query = getCardQueryState(card.circuit.id)
    const controls = (
      <Space wrap size="small" style={{ width: '100%', justifyContent: 'flex-end' }}>
        <Select value={query.rangeValue} size="small" style={{ width: 132 }} options={RANGE_OPTIONS} onChange={(value) => handleCardRangeChange(card.circuit.id, value)} />
        {query.rangeValue === 'custom' ? (
          <RangePicker size="small" showTime value={query.customRange as any} onChange={(value) => handleCardCustomRangeChange(card.circuit.id, value as any)} />
        ) : null}
        <Select value={query.intervalValue} size="small" style={{ width: 104 }} options={INTERVAL_OPTIONS} onChange={(value) => handleCardIntervalChange(card.circuit.id, value)} />
        <Button size="small" icon={<ReloadOutlined />} loading={card.loading} onClick={() => { void reloadCardById(card.circuit.id, true) }} />
        {!isSummaryCard ? <Button size="small" type="text" danger icon={<CloseOutlined />} onClick={() => removeCard(card.circuit.id)} /> : null}
      </Space>
    )
    return (
      <Card
        key={card.circuit.id}
        draggable={false}
        onDragOver={(event) => {
          if (!layoutLocked && !isSummaryCard) event.preventDefault()
        }}
        onDrop={(event) => {
          event.preventDefault()
          if (draggingId && !isSummaryCard) reorderVisibleCards(draggingId, card.circuit.id)
          setDraggingId(null)
        }}
        styles={{ body: { padding: 14 } }}
      >
        <Spin spinning={Boolean(card.loading)} tip="正在读取接口历史流量...">
          {card.error ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={card.error} />
          ) : (
            <Space direction="vertical" size="middle" style={{ width: '100%' }}>
              {!isSummaryCard ? (
                <HolderOutlined
                  draggable={!layoutLocked}
                  onDragStart={() => {
                    if (!layoutLocked) setDraggingId(card.circuit.id)
                  }}
                  style={{ color: layoutLocked ? '#d9d9d9' : '#8c8c8c', cursor: layoutLocked ? 'not-allowed' : 'grab', alignSelf: 'flex-start' }}
                />
              ) : null}
              <TrafficChart
                title={getCardTitle(card)}
                subtitle={isSummaryCard ? getSummaryCardSubtitle(summaryPresets.find((preset) => getSummaryPresetId(preset) === card.circuit.id) || DEFAULT_SUMMARY_PRESETS[0]) : getCardSubtitle(card)}
                data={aggregateData}
                scale={chartScale}
              />
              {showTargetCharts ? (
                <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr)', gap: 16 }}>
                  {card.targets.map((target) => (
                    <TrafficChart
                      key={`${card.circuit.id}-${target.deviceIp}-${target.portName}-${target.side}`}
                      title={getCardTitle(card, target)}
                      subtitle={getCardSubtitle(card, target)}
                      data={target.data}
                      scale={getChartScale(target.data)}
                    />
                  ))}
                </div>
              ) : null}
              {controls}
            </Space>
          )}
        </Spin>
      </Card>
    )
  }

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <div style={{ color: '#8c8c8c', fontSize: 13 }}>监控中心 / 流量查询</div>
      <Card title="流量查询">
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Space wrap align="center">
            <Select
              value={lineType}
              style={{ width: 140 }}
              options={[
                { label: '公网资源', value: 'internet' },
                { label: '专线资源', value: 'private_line' },
              ]}
              onChange={setLineType}
            />
            <Select
              allowClear
              showSearch
              loading={optionsLoading}
              value={datacenterId}
              placeholder="选择机房"
              optionFilterProp="label"
              style={{ width: 180 }}
              options={datacenters.map((item) => ({ label: item.name, value: item.id }))}
              onChange={setDatacenterId}
            />
            {lineType === 'internet' ? (
              <Select
                allowClear
                showSearch
                loading={optionsLoading}
                value={providerKey}
                placeholder="选择运营商/供应商"
                optionFilterProp="label"
                style={{ width: 190 }}
                options={providerOptions}
                onChange={setProviderKey}
              />
            ) : (
              <Select
                allowClear
                showSearch
                loading={optionsLoading}
                value={customerId}
                placeholder="选择客户"
                optionFilterProp="label"
                style={{ width: 190 }}
                options={customerOptions}
                onChange={setCustomerId}
              />
            )}
            <Select
              allowClear
              showSearch
              value={selectedLineId}
              placeholder="线路名称"
              optionFilterProp="searchText"
              optionLabelProp="labelText"
              listHeight={320}
              style={{ width: 420 }}
              options={lineOptions}
              onChange={handleSelectLine}
            />
            <Select
              mode="multiple"
              value={selectedPresetKeys.filter((key) => !DEFAULT_SUMMARY_PRESETS.some((item) => item.key === key))}
              placeholder="选择汇总视图"
              style={{ width: 360 }}
              optionFilterProp="label"
              options={summaryPresets.map((item) => ({ label: item.label, value: item.key }))}
              onChange={(values) => { void handleSelectPresets(values as string[]) }}
              maxTagCount="responsive"
              maxTagPlaceholder={(omittedValues) => `+${omittedValues.length}`}
            />
            <Input
              allowClear
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              prefix={<SearchOutlined />}
              placeholder={lineType === 'internet' ? '搜索公网名称、机房、运营商、设备或接口' : '搜索专线名称、机房、客户、设备或接口'}
              style={{ width: 420 }}
            />
            <Button onClick={resetFilters}>重置</Button>
          </Space>
        </Space>
      </Card>

      <Card
        title="流量图表"
        extra={(
          <Space wrap>
            <Button icon={<ReloadOutlined />} loading={dashboardLoading} disabled={!visibleCards.length && !summaryCards.length} onClick={refreshVisibleCards}>
              刷新全部
            </Button>
            <Button icon={layoutLocked ? <LockOutlined /> : <UnlockOutlined />} onClick={toggleLayoutLocked}>
              {layoutLocked ? '已锁定位置' : '可拖动排序'}
            </Button>
          </Space>
        )}
      >
        <Spin spinning={dashboardLoading} tip="正在加载默认图表...">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 16 }}>
            {summaryCards.map(renderTrafficCard)}
            {visibleNonDuplicateCards.map(renderTrafficCard)}
          </div>
        </Spin>
      </Card>
    </Space>
  )
}

export default TrafficQuery
