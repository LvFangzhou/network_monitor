import { type Key, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  Button,
  Card,
  DatePicker,
  Form,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Popover,
  Segmented,
  Select,
  Space,
  Spin,
  Switch,
  Tag,
  Typography,
  message,
} from 'antd'
import { DeleteOutlined, EditOutlined, PlusOutlined, ReloadOutlined, ThunderboltOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { CartesianGrid, Legend, Line, LineChart, ReferenceArea, ResponsiveContainer, Tooltip as ChartTooltip, XAxis, YAxis } from 'recharts'
import {
  createQualityProbeTarget,
  deleteQualityProbeTarget,
  getQualityProbeHistory,
  getCircuitTrafficHistory,
  getQualityNqaInstances,
  getQualityTargetAlertSettings,
  getQualityProbeTargets,
  runQualityProbeMtr,
  saveQualityTargetAlertSettings,
  testQualityProbeTarget,
  updateQualityProbeTarget,
  type QualityProbeHistoryPoint,
  type QualityNqaInstance,
  type QualityProbeTarget,
} from '../../api/metrics'
import { testAlertNotification } from '../../api/alerts'
import { getDatacenters, getDevices, type Datacenter, type Device } from '../../api/devices'
import { getCircuits, type Circuit } from '../../api/resources'

const { Text, Title } = Typography
const { RangePicker } = DatePicker

const nqaInstanceValue = (adminName?: string | null, operationTag?: string | null) =>
  `${encodeURIComponent(adminName || '')}|${encodeURIComponent(operationTag || '')}`

const formatTime = (value?: string | null) => {
  if (!value) return '-'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}

type QualityHealthLevel = 'critical' | 'warning' | 'notice' | 'healthy' | 'no_data'

const qualityHealthStyles: Record<QualityHealthLevel, { label: string; color: string; background: string; border: string; rank: number }> = {
  critical: { label: '严重', color: '#cf1322', background: '#fff1f0', border: '#ff7875', rank: 0 },
  warning: { label: '异常', color: '#d46b08', background: '#fff7e6', border: '#ffa940', rank: 1 },
  notice: { label: '关注', color: '#ad8b00', background: '#feffe6', border: '#fadb14', rank: 2 },
  healthy: { label: '正常', color: '#237804', background: '#f6ffed', border: '#73d13d', rank: 3 },
  no_data: { label: '无数据', color: '#595959', background: '#fafafa', border: '#d9d9d9', rank: 4 },
}

const getQualityAvailability = (record: QualityProbeTarget) => {
  if (record.last_availability_percent !== null && record.last_availability_percent !== undefined) {
    return Math.max(0, Math.min(100, Number(record.last_availability_percent)))
  }
  if (record.last_packet_loss_percent !== null && record.last_packet_loss_percent !== undefined) {
    return Math.max(0, Math.min(100, 100 - Number(record.last_packet_loss_percent)))
  }
  return null
}

const getQualityHealthLevel = (record: QualityProbeTarget): QualityHealthLevel => {
  const latency = record.last_avg_latency_ms
  const loss = record.last_packet_loss_percent
  if (record.last_success === null || record.last_success === undefined || (latency == null && loss == null)) return 'no_data'
  if (record.last_success === false) return 'critical'

  const latencyThreshold = Math.max(1, Number(record.latency_threshold_ms || 1))
  const lossThreshold = Math.max(0.01, Number(record.loss_threshold_percent || 0.01))
  const latencyRatio = latency == null ? 0 : Number(latency) / latencyThreshold
  const lossRatio = loss == null ? 0 : Number(loss) / lossThreshold
  const maxRatio = Math.max(latencyRatio, lossRatio)
  if (maxRatio >= 3 || (loss != null && Number(loss) >= 10)) return 'critical'
  if (maxRatio >= 1) return 'warning'
  if (maxRatio >= 0.8) return 'notice'
  return 'healthy'
}

const getQualityHealthReason = (record: QualityProbeTarget) => {
  const level = getQualityHealthLevel(record)
  if (level === 'no_data') return '无探测数据'
  if (record.last_success === false) return '探测不可达'

  const latencyRatio = record.last_avg_latency_ms == null
    ? 0
    : Number(record.last_avg_latency_ms) / Math.max(1, Number(record.latency_threshold_ms || 1))
  const lossRatio = record.last_packet_loss_percent == null
    ? 0
    : Number(record.last_packet_loss_percent) / Math.max(0.01, Number(record.loss_threshold_percent || 0.01))
  const affected = [
    latencyRatio >= 0.8 ? '延迟' : '',
    lossRatio >= 0.8 ? '丢包' : '',
  ].filter(Boolean).join('/')
  if (level === 'critical') return `${affected || '质量'}严重`
  if (level === 'warning') return `${affected || '质量'}超阈值`
  if (level === 'notice') return `${affected || '质量'}接近阈值`
  return '质量正常'
}

const formatDuration = (durationMs: number) => {
  const totalSeconds = Math.max(0, Math.round(durationMs / 1000))
  if (totalSeconds < 60) return `${totalSeconds}秒`
  const minutes = Math.floor(totalSeconds / 60)
  if (minutes < 60) return `${minutes}分${totalSeconds % 60 ? `${totalSeconds % 60}秒` : ''}`
  const hours = Math.floor(minutes / 60)
  return `${hours}小时${minutes % 60 ? `${minutes % 60}分` : ''}`
}

const formatBps = (value?: number | null) => {
  const safe = Math.abs(Number(value || 0))
  if (safe >= 1_000_000_000) return `${(Number(value || 0) / 1_000_000_000).toFixed(2)} Gbps`
  if (safe >= 1_000_000) return `${(Number(value || 0) / 1_000_000).toFixed(2)} Mbps`
  if (safe >= 1_000) return `${(Number(value || 0) / 1_000).toFixed(2)} Kbps`
  return `${Number(value || 0).toFixed(0)} bps`
}

type LinkedTrafficPoint = {
  ts: number
  in_bps: number
  out_bps: number
}

const rangeOptions = [
  { label: '15分钟', value: '-15m', interval: '1s' },
  { label: '1小时', value: '-1h', interval: '5s' },
  { label: '6小时', value: '-6h', interval: '30s' },
  { label: '24小时', value: '-24h', interval: '2m' },
  { label: '7天', value: '-7d', interval: '15m' },
  { label: '30天', value: '-30d', interval: '1h' },
  { label: '365天', value: '-365d', interval: '1d' },
  { label: '自定义时间', value: 'custom', interval: '1m' },
]

const formatChartTime = (value: string | number, rangeValue: string) => {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  if (rangeValue === '-15m' || rangeValue === '-1h') {
    return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}:${String(date.getSeconds()).padStart(2, '0')}`
  }
  if (rangeValue === '-6h' || rangeValue === '-24h') {
    return `${String(date.getHours()).padStart(2, '0')}:${String(date.getMinutes()).padStart(2, '0')}`
  }
  return `${date.getMonth() + 1}-${date.getDate()}`
}

const formatZoomedChartTime = (value: string | number, visibleSpanMs: number, rangeValue: string) => {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return String(value)
  const hour = String(date.getHours()).padStart(2, '0')
  const minute = String(date.getMinutes()).padStart(2, '0')
  const second = String(date.getSeconds()).padStart(2, '0')
  if (visibleSpanMs <= 2 * 60 * 60 * 1000) return `${hour}:${minute}:${second}`
  if (visibleSpanMs <= 48 * 60 * 60 * 1000) return `${date.getMonth() + 1}-${date.getDate()} ${hour}:${minute}`
  return formatChartTime(value, rangeValue)
}

const chartTickCount = (rangeValue: string) => {
  if (rangeValue === '-15m') return 16
  if (rangeValue === '-1h') return 13
  if (rangeValue === '-6h') return 13
  if (rangeValue === '-24h') return 13
  if (rangeValue === '-7d') return 8
  if (rangeValue === '-30d') return 10
  return 13
}

const getCustomInterval = (customRange?: [dayjs.Dayjs | null, dayjs.Dayjs | null] | null) => {
  if (!customRange?.[0] || !customRange?.[1]) return '1m'
  const seconds = Math.max(1, customRange[1].diff(customRange[0], 'second'))
  if (seconds <= 30 * 60) return '1s'
  if (seconds <= 2 * 60 * 60) return '5s'
  if (seconds <= 12 * 60 * 60) return '30s'
  if (seconds <= 3 * 24 * 60 * 60) return '2m'
  if (seconds <= 7 * 24 * 60 * 60) return '15m'
  if (seconds <= 30 * 24 * 60 * 60) return '1h'
  return '1d'
}

const buildQualityHistoryParams = (rangeValue: string, customRange?: [dayjs.Dayjs | null, dayjs.Dayjs | null] | null) => {
  const option = rangeOptions.find((item) => item.value === rangeValue) || rangeOptions[1]
  if (rangeValue === 'custom' && customRange?.[0] && customRange?.[1]) {
    return {
      range: '-24h',
      interval: getCustomInterval(customRange),
      start_ts: customRange[0].valueOf(),
      end_ts: customRange[1].valueOf(),
    }
  }
  return { range: option.value === 'custom' ? '-24h' : option.value, interval: option.interval }
}

const QualityChartPanel = ({ target }: { target: QualityProbeTarget }) => {
  const [rangeValue, setRangeValue] = useState('-1h')
  const [customRange, setCustomRange] = useState<[dayjs.Dayjs | null, dayjs.Dayjs | null] | null>([dayjs().subtract(1, 'hour'), dayjs()])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyData, setHistoryData] = useState<QualityProbeHistoryPoint[]>([])
  const [trafficLoading, setTrafficLoading] = useState(false)
  const [trafficData, setTrafficData] = useState<LinkedTrafficPoint[]>([])
  const [autoRefresh, setAutoRefresh] = useState(true)
  const [refreshCountdown, setRefreshCountdown] = useState(10)
  const [zoomDomain, setZoomDomain] = useState<[number, number] | null>(null)
  const [selectionStart, setSelectionStart] = useState<number | null>(null)
  const [selectionEnd, setSelectionEnd] = useState<number | null>(null)
  const [displayMode, setDisplayMode] = useState<'realtime' | 'smooth5m'>('realtime')
  const historyRequestSeqRef = useRef(0)
  const trafficRequestSeqRef = useRef(0)

  const fetchHistory = useCallback(async (silent = false) => {
    const requestSeq = historyRequestSeqRef.current + 1
    historyRequestSeqRef.current = requestSeq
    const params = buildQualityHistoryParams(rangeValue, customRange)
    if (!silent) setHistoryLoading(true)
    try {
      const response = await getQualityProbeHistory(target.id, params)
      if (historyRequestSeqRef.current !== requestSeq) return
      setHistoryData(response.data || [])
    } catch (error: any) {
      if (historyRequestSeqRef.current !== requestSeq) return
      if (!silent) message.error(error?.response?.data?.detail || '获取延迟变化曲线失败')
      setHistoryData([])
    } finally {
      if (historyRequestSeqRef.current === requestSeq && !silent) setHistoryLoading(false)
    }
  }, [rangeValue, customRange, target.id])

  const fetchLinkedTraffic = useCallback(async (silent = false) => {
    if (!target.circuit_id) {
      setTrafficData([])
      return
    }
    const requestSeq = trafficRequestSeqRef.current + 1
    trafficRequestSeqRef.current = requestSeq
    const params = buildQualityHistoryParams(rangeValue, customRange)
    if (!silent) setTrafficLoading(true)
    try {
      const response = await getCircuitTrafficHistory(target.circuit_id, params)
      if (trafficRequestSeqRef.current !== requestSeq) return
      const rows = response.aggregate || response.data || []
      setTrafficData(rows.map((point) => ({
        ts: new Date(String(point._time || point.time || '')).getTime(),
        in_bps: Number(point.in_bps || 0),
        out_bps: Number(point.out_bps || 0),
      })).filter((point) => Number.isFinite(point.ts)).sort((left, right) => left.ts - right.ts))
    } catch (error: any) {
      if (trafficRequestSeqRef.current !== requestSeq) return
      if (!silent) message.warning(error?.response?.data?.detail || '关联线路流量读取失败')
      setTrafficData([])
    } finally {
      if (trafficRequestSeqRef.current === requestSeq && !silent) setTrafficLoading(false)
    }
  }, [customRange, rangeValue, target.circuit_id])

  useEffect(() => {
    void fetchHistory()
    void fetchLinkedTraffic()
    setRefreshCountdown(10)
  }, [fetchHistory, fetchLinkedTraffic])

  useEffect(() => {
    setZoomDomain(null)
    setSelectionStart(null)
    setSelectionEnd(null)
  }, [rangeValue, customRange, target.id])

  useEffect(() => {
    if (!autoRefresh) return undefined
    const timer = window.setInterval(() => {
      setRefreshCountdown((value) => {
        if (value <= 1) {
          void fetchHistory(true)
          void fetchLinkedTraffic(true)
          return 10
        }
        return value - 1
      })
    }, 1000)
    return () => window.clearInterval(timer)
  }, [autoRefresh, fetchHistory, fetchLinkedTraffic])

  const chartData = useMemo(
    () => historyData
      .filter((point) => point._time)
      .map((point) => ({
        ...point,
        ts: new Date(point._time || '').getTime(),
      }))
      .filter((point) => Number.isFinite(point.ts)),
    [historyData]
  )

  const displayChartData = useMemo(() => {
    if (displayMode === 'realtime' || chartData.length < 2) return chartData

    const windowMs = 5 * 60 * 1000
    let start = 0
    let latencySum = 0
    let latencyCount = 0
    let lossSum = 0
    let lossCount = 0
    return chartData.map((point, index) => {
      const latency = Number(point.avg_latency_ms)
      const loss = Number(point.packet_loss_percent)
      if (Number.isFinite(latency)) {
        latencySum += latency
        latencyCount += 1
      }
      if (Number.isFinite(loss)) {
        lossSum += loss
        lossCount += 1
      }
      while (start < index && chartData[start].ts < point.ts - windowMs) {
        const expiredLatency = Number(chartData[start].avg_latency_ms)
        const expiredLoss = Number(chartData[start].packet_loss_percent)
        if (Number.isFinite(expiredLatency)) {
          latencySum -= expiredLatency
          latencyCount -= 1
        }
        if (Number.isFinite(expiredLoss)) {
          lossSum -= expiredLoss
          lossCount -= 1
        }
        start += 1
      }
      return {
        ...point,
        avg_latency_ms: latencyCount ? latencySum / latencyCount : null,
        packet_loss_percent: lossCount ? lossSum / lossCount : null,
      }
    })
  }, [chartData, displayMode])

  const visibleSpanMs = useMemo(() => {
    if (zoomDomain) return Math.max(1, zoomDomain[1] - zoomDomain[0])
    if (chartData.length < 2) return 0
    return Math.max(1, chartData[chartData.length - 1].ts - chartData[0].ts)
  }, [chartData, zoomDomain])

  const visibleRawData = useMemo(
    () => zoomDomain
      ? chartData.filter((point) => point.ts >= zoomDomain[0] && point.ts <= zoomDomain[1])
      : chartData,
    [chartData, zoomDomain]
  )

  const historySummary = useMemo(() => {
    const latencyValues = visibleRawData
      .map((point) => Number(point.avg_latency_ms))
      .filter(Number.isFinite)
    const lossValues = visibleRawData
      .map((point) => Number(point.packet_loss_percent))
      .filter(Number.isFinite)
    const availabilityValues = visibleRawData
      .map((point) => {
        const availability = Number(point.availability_percent)
        if (Number.isFinite(availability)) return availability
        const loss = Number(point.packet_loss_percent)
        return Number.isFinite(loss) ? Math.max(0, 100 - loss) : Number.NaN
      })
      .filter(Number.isFinite)
    const sortedLatency = [...latencyValues].sort((left, right) => left - right)
    const p95Index = Math.max(0, Math.ceil(sortedLatency.length * 0.95) - 1)
    return {
      availability: availabilityValues.length
        ? availabilityValues.reduce((sum, value) => sum + value, 0) / availabilityValues.length
        : null,
      averageLatency: latencyValues.length
        ? latencyValues.reduce((sum, value) => sum + value, 0) / latencyValues.length
        : null,
      p95Latency: sortedLatency.length ? sortedLatency[p95Index] : null,
      maximumLatency: latencyValues.length ? Math.max(...latencyValues) : null,
      averageLoss: lossValues.length ? lossValues.reduce((sum, value) => sum + value, 0) / lossValues.length : null,
      maximumLoss: lossValues.length ? Math.max(...lossValues) : null,
    }
  }, [visibleRawData])

  const abnormalEvents = useMemo(() => {
    if (!visibleRawData.length) return []
    const ordered = [...visibleRawData].sort((left, right) => left.ts - right.ts)
    const gaps = ordered.slice(1)
      .map((point, index) => point.ts - ordered[index].ts)
      .filter((value) => value > 0)
      .sort((left, right) => left - right)
    const observedInterval = gaps.length ? gaps[Math.floor(gaps.length / 2)] : target.interval_seconds * 1000
    const nominalInterval = Math.max(1000, observedInterval || target.interval_seconds * 1000)
    const gapLimit = nominalInterval * 2.5
    type AbnormalEvent = {
      start: number
      end: number
      reasons: Set<string>
      maximumLatency: number
      maximumLoss: number
      durationMs: number
    }
    const events: AbnormalEvent[] = []
    let current: AbnormalEvent | null = null

    const closeCurrent = () => {
      if (!current) return
      current.durationMs = Math.max(nominalInterval, current.end - current.start + nominalInterval)
      events.push(current)
      current = null
    }

    ordered.forEach((point) => {
      const latency = Number(point.avg_latency_ms)
      const loss = Number(point.packet_loss_percent)
      const reasons: string[] = []
      if (Number.isFinite(latency) && latency >= target.latency_threshold_ms) reasons.push('延迟达到阈值')
      if (Number.isFinite(loss) && loss >= target.loss_threshold_percent) reasons.push('丢包达到阈值')
      if (!reasons.length) {
        closeCurrent()
        return
      }
      if (!current || point.ts - current.end > gapLimit) {
        closeCurrent()
        current = {
          start: point.ts,
          end: point.ts,
          reasons: new Set(reasons),
          maximumLatency: Number.isFinite(latency) ? latency : 0,
          maximumLoss: Number.isFinite(loss) ? loss : 0,
          durationMs: nominalInterval,
        }
        return
      }
      current.end = point.ts
      reasons.forEach((reason) => current?.reasons.add(reason))
      if (Number.isFinite(latency)) current.maximumLatency = Math.max(current.maximumLatency, latency)
      if (Number.isFinite(loss)) current.maximumLoss = Math.max(current.maximumLoss, loss)
    })
    closeCurrent()
    return events.reverse()
  }, [target.interval_seconds, target.latency_threshold_ms, target.loss_threshold_percent, visibleRawData])

  const trafficScale = useMemo(() => {
    const maximum = Math.max(0, ...trafficData.flatMap((point) => [point.in_bps, point.out_bps]))
    if (maximum >= 1_000_000_000) return { divisor: 1_000_000_000, unit: 'Gbps' }
    if (maximum >= 1_000_000) return { divisor: 1_000_000, unit: 'Mbps' }
    if (maximum >= 1_000) return { divisor: 1_000, unit: 'Kbps' }
    return { divisor: 1, unit: 'bps' }
  }, [trafficData])

  const correlation = useMemo(() => {
    if (!target.circuit_id) return null
    const lineLabel = target.probe_source === 'device_nqa_snmp' ? '专线' : '公网线路'
    if (!trafficData.length) return { tone: '#8c8c8c', text: `已关联${lineLabel}，但当前时间范围没有读取到线路流量。` }
    const latest = trafficData[trafficData.length - 1]
    const currentBps = Math.max(latest.in_bps, latest.out_bps)
    const capacityBps = Number(target.circuit_bandwidth_mbps || 0) * 1_000_000
    const utilization = capacityBps > 0 ? currentBps / capacityBps * 100 : null
    const level = getQualityHealthLevel(target)
    if (level === 'healthy' || level === 'notice') {
      return {
        tone: '#237804',
        text: `当前质量未明显异常；出口流量 ${formatBps(currentBps)}${utilization == null ? '' : `，约占线路带宽 ${utilization.toFixed(1)}%`}。`,
      }
    }
    if (utilization !== null && utilization >= 80) {
      return {
        tone: '#cf1322',
        text: `质量异常时出口利用率约 ${utilization.toFixed(1)}%，疑似出口拥塞，建议先检查接口队列丢包和带宽利用率。`,
      }
    }
    return {
      tone: '#d46b08',
      text: `质量指标异常，但出口流量${utilization == null ? '未配置带宽，无法计算利用率' : `仅约占带宽 ${utilization.toFixed(1)}%`}，建议优先检查运营商路径并执行 MTR。`,
    }
  }, [target, trafficData])

  const finishZoomSelection = () => {
    if (selectionStart !== null && selectionEnd !== null && selectionStart !== selectionEnd) {
      const start = Math.min(selectionStart, selectionEnd)
      const end = Math.max(selectionStart, selectionEnd)
      setZoomDomain([start, end])
    }
    setSelectionStart(null)
    setSelectionEnd(null)
  }

  return (
    <Card
      size="small"
      title={`${target.name} / ${target.target} 质量变化`}
      extra={(
        <Space wrap>
          <Segmented
            size="small"
            value={displayMode}
            options={[
              { label: '实时值', value: 'realtime' },
              { label: '5分钟平滑', value: 'smooth5m' },
            ]}
            onChange={(value) => setDisplayMode(value as 'realtime' | 'smooth5m')}
          />
          <Select
            value={rangeValue}
            style={{ width: 120 }}
            options={rangeOptions.map((item) => ({ label: item.label, value: item.value }))}
            onChange={setRangeValue}
          />
          {rangeValue === 'custom' && (
            <RangePicker
              showTime
              value={customRange as any}
              allowClear={false}
              onChange={(value) => setCustomRange(value as [dayjs.Dayjs | null, dayjs.Dayjs | null] | null)}
              style={{ width: 360 }}
            />
          )}
          <Switch
            checked={autoRefresh}
            checkedChildren="自动"
            unCheckedChildren="手动"
            onChange={(checked) => {
              setAutoRefresh(checked)
              setRefreshCountdown(10)
            }}
          />
          <Text type="secondary" style={{ width: 92, textAlign: 'right' }}>
            {autoRefresh ? `${refreshCountdown}s 后刷新` : '手动刷新'}
          </Text>
          <Button
            icon={<ReloadOutlined />}
            loading={historyLoading}
            onClick={() => {
              setRefreshCountdown(10)
              void fetchHistory()
              void fetchLinkedTraffic()
            }}
          >
            刷新
          </Button>
          {zoomDomain ? (
            <Button onClick={() => setZoomDomain(null)}>重置缩放</Button>
          ) : null}
        </Space>
      )}
      styles={{ body: { paddingTop: 8 } }}
    >
      <Text type="secondary" style={{ fontSize: 12 }}>
        在曲线上按住鼠标拖选一段时间即可放大，缩放只作用于当前线路；点击“重置缩放”恢复完整范围。展开某条线路后才加载曲线，并默认 10 秒自动刷新；未展开的线路不会请求历史曲线。丢包率按滚动窗口内发送和收到包数汇总计算，避免单包丢失时在 0% 和 100% 之间跳变。
      </Text>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(135px, 1fr))', gap: 8, marginTop: 10 }}>
        {[
          { label: '范围内可用率', value: historySummary.availability, unit: '%' },
          { label: '平均延迟', value: historySummary.averageLatency, unit: 'ms' },
          { label: 'P95 延迟', value: historySummary.p95Latency, unit: 'ms' },
          { label: '最大延迟', value: historySummary.maximumLatency, unit: 'ms' },
          { label: '平均丢包率', value: historySummary.averageLoss, unit: '%' },
          { label: '最大丢包率', value: historySummary.maximumLoss, unit: '%' },
        ].map((item) => (
          <div key={item.label} style={{ padding: '8px 10px', border: '1px solid #f0f0f0', borderRadius: 6, background: '#fafafa' }}>
            <Text type="secondary" style={{ display: 'block', fontSize: 12 }}>{item.label}</Text>
            <Text strong style={{ fontSize: 17 }}>
              {item.value == null ? '-' : `${item.value.toFixed(2)} ${item.unit}`}
            </Text>
          </div>
        ))}
      </div>
      <div style={{ height: 320, width: '100%', marginTop: 8, cursor: 'crosshair', userSelect: 'none' }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart
            data={displayChartData}
            margin={{ top: 16, right: 36, left: 8, bottom: 16 }}
            onMouseDown={(event: any) => {
              if (Number.isFinite(Number(event?.activeLabel))) {
                const value = Number(event.activeLabel)
                setSelectionStart(value)
                setSelectionEnd(value)
              }
            }}
            onMouseMove={(event: any) => {
              if (selectionStart !== null && Number.isFinite(Number(event?.activeLabel))) {
                setSelectionEnd(Number(event.activeLabel))
              }
            }}
            onMouseUp={finishZoomSelection}
            onMouseLeave={() => {
              if (selectionStart !== null) finishZoomSelection()
            }}
          >
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis
              dataKey="ts"
              type="number"
              scale="time"
              domain={zoomDomain || ['dataMin', 'dataMax']}
              allowDataOverflow={Boolean(zoomDomain)}
              tick={{ fontSize: 11 }}
              minTickGap={8}
              tickCount={chartTickCount(rangeValue)}
              tickFormatter={(value) => formatZoomedChartTime(Number(value), visibleSpanMs, rangeValue)}
            />
            <YAxis
              yAxisId="ms"
              width={82}
              tick={{ fontSize: 11 }}
              tickFormatter={(value) => `${value} ms`}
            />
            <YAxis
              yAxisId="percent"
              orientation="right"
              width={82}
              tick={{ fontSize: 11 }}
              domain={[0, 100]}
              tickFormatter={(value) => `${value}%`}
            />
            <ChartTooltip
              labelFormatter={(value) => formatTime(new Date(Number(value)).toISOString())}
              formatter={(value: any, name: string) => {
                const unit = name.includes('丢包') ? '%' : 'ms'
                return [`${Number(value).toFixed(2)} ${unit}`, name]
              }}
            />
            <Legend />
            {selectionStart !== null && selectionEnd !== null ? (
              <ReferenceArea
                x1={Math.min(selectionStart, selectionEnd)}
                x2={Math.max(selectionStart, selectionEnd)}
                yAxisId="ms"
                strokeOpacity={0.35}
                fill="#1677ff"
                fillOpacity={0.16}
              />
            ) : null}
            <Line yAxisId="ms" type="monotone" dataKey="avg_latency_ms" name="延迟" stroke="#1677ff" dot={false} strokeWidth={2} connectNulls isAnimationActive={false} />
            {target.probe_source === 'device_nqa_snmp' ? (
              <Line yAxisId="ms" type="monotone" dataKey="jitter_ms" name="NQA抖动" stroke="#722ed1" dot={false} strokeWidth={2} connectNulls isAnimationActive={false} />
            ) : null}
            <Line yAxisId="percent" type="monotone" dataKey="packet_loss_percent" name="丢包率" stroke="#f5222d" dot={false} strokeWidth={2} connectNulls isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
      {!historyLoading && !chartData.length ? (
        <Text type="secondary">当前时间范围内还没有曲线数据，后台任务采集到新点后会自动刷新。</Text>
      ) : null}
      {target.circuit_id ? (
        <Card
          size="small"
          title={`关联${target.probe_source === 'device_nqa_snmp' ? '专线' : '出口'}流量：${target.circuit_name || `线路 #${target.circuit_id}`}`}
          extra={<Text type="secondary">{[target.circuit_device_name || target.circuit_device_ip, target.circuit_port_name].filter(Boolean).join(' / ')}</Text>}
          style={{ marginTop: 12 }}
          loading={trafficLoading}
        >
          {correlation ? (
            <div style={{ padding: '8px 10px', marginBottom: 8, borderRadius: 6, background: '#fafafa', borderLeft: `3px solid ${correlation.tone}` }}>
              <Text style={{ color: correlation.tone }}>{correlation.text}</Text>
            </div>
          ) : null}
          {trafficData.length ? (
            <div style={{ height: 220, width: '100%' }}>
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={trafficData} margin={{ top: 10, right: 24, left: 8, bottom: 8 }}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis
                    dataKey="ts"
                    type="number"
                    scale="time"
                    domain={zoomDomain || ['dataMin', 'dataMax']}
                    allowDataOverflow={Boolean(zoomDomain)}
                    tick={{ fontSize: 11 }}
                    tickFormatter={(value) => formatZoomedChartTime(Number(value), visibleSpanMs, rangeValue)}
                  />
                  <YAxis width={78} tick={{ fontSize: 11 }} tickFormatter={(value) => `${(Number(value) / trafficScale.divisor).toFixed(1)} ${trafficScale.unit}`} />
                  <ChartTooltip
                    labelFormatter={(value) => formatTime(new Date(Number(value)).toISOString())}
                    formatter={(value: any, name: string) => [formatBps(Number(value)), name]}
                  />
                  <Legend />
                  <Line type="monotone" dataKey="in_bps" name="入方向" stroke="#52c41a" dot={false} strokeWidth={2} connectNulls isAnimationActive={false} />
                  <Line type="monotone" dataKey="out_bps" name="出方向" stroke="#1677ff" dot={false} strokeWidth={2} connectNulls isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          ) : <Text type="secondary">当前时间范围暂无关联线路流量。</Text>}
        </Card>
      ) : (
        <div style={{ marginTop: 10, padding: '8px 10px', background: '#fafafa', borderRadius: 6 }}>
          <Text type="secondary">
            {target.probe_source === 'device_nqa_snmp'
              ? '尚未关联专线。编辑该探测目标并选择专线后，可同步查看线路流量及设备、接口信息。'
              : '尚未关联公网线路。编辑该探测目标并选择公网线路后，可同步查看出口流量和关联判断。'}
          </Text>
        </div>
      )}
      {chartData.length ? (
        <div style={{ marginTop: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 7 }}>
            <Text strong>异常时间轴（当前显示范围）</Text>
            <Text type="secondary">{abnormalEvents.length} 段异常</Text>
          </div>
          {abnormalEvents.length ? (
            <div style={{ maxHeight: 210, overflowY: 'auto', border: '1px solid #f0f0f0', borderRadius: 6 }}>
              {abnormalEvents.slice(0, 30).map((event, index) => (
                <div
                  key={`${event.start}-${index}`}
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'minmax(130px, 0.7fr) minmax(250px, 1.4fr) 100px minmax(220px, 1fr)',
                    gap: 12,
                    alignItems: 'center',
                    padding: '8px 10px',
                    borderBottom: index === Math.min(abnormalEvents.length, 30) - 1 ? 'none' : '1px solid #f5f5f5',
                  }}
                >
                  <Space size={4} wrap>
                    {[...event.reasons].map((reason) => <Tag color="red" key={reason}>{reason}</Tag>)}
                  </Space>
                  <Text>{formatTime(new Date(event.start).toISOString())} ～ {formatTime(new Date(event.end).toISOString())}</Text>
                  <Text>{formatDuration(event.durationMs)}</Text>
                  <Text type="secondary">
                    峰值延迟 {event.maximumLatency.toFixed(2)} ms / 丢包 {event.maximumLoss.toFixed(2)}%
                  </Text>
                </div>
              ))}
            </div>
          ) : <Text type="secondary">当前显示范围内没有超过该线路阈值的记录。</Text>}
        </div>
      ) : null}
    </Card>
  )
}

const QualityQuery = () => {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testingId, setTestingId] = useState<number | null>(null)
  const [items, setItems] = useState<QualityProbeTarget[]>([])
  const [datacenters, setDatacenters] = useState<Datacenter[]>([])
  const [circuits, setCircuits] = useState<Circuit[]>([])
  const [keyword, setKeyword] = useState('')
  const [activeFilter, setActiveFilter] = useState<string>('all')
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<QualityProbeTarget | null>(null)
  const [expandedRowKeys, setExpandedRowKeys] = useState<Key[]>([])
  const [mtrLoadingId, setMtrLoadingId] = useState<number | null>(null)
  const [mtrOpen, setMtrOpen] = useState(false)
  const [mtrTitle, setMtrTitle] = useState('')
  const [mtrCommand, setMtrCommand] = useState('')
  const [mtrOutput, setMtrOutput] = useState('')
  const [targetAlertSettingsLoading, setTargetAlertSettingsLoading] = useState(false)
  const [targetAlertTesting, setTargetAlertTesting] = useState(false)
  const [nqaDevices, setNqaDevices] = useState<Device[]>([])
  const [nqaDevicesLoading, setNqaDevicesLoading] = useState(false)
  const [nqaInstances, setNqaInstances] = useState<QualityNqaInstance[]>([])
  const [nqaInstancesLoading, setNqaInstancesLoading] = useState(false)
  const nqaDeviceSearchTimer = useRef<number | null>(null)
  const probeSource = Form.useWatch('probe_source', form) || 'server_icmp'
  const selectedNqaDeviceId = Form.useWatch('device_id', form)

  const testTargetAlertRobot = async () => {
    const values = await form.validateFields(['alert_webhook_url', 'alert_mention_users_text'])
    setTargetAlertTesting(true)
    try {
      const mentionUsers = String(values.alert_mention_users_text || '')
        .split(/[,，;；\s]+/)
        .map((item) => item.trim())
        .filter(Boolean)
      const result = await testAlertNotification(values.alert_webhook_url, mentionUsers)
      message.success(result.message || '测试消息发送成功')
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '测试消息发送失败')
    } finally {
      setTargetAlertTesting(false)
    }
  }

  const fetchItems = async (silent = false) => {
    if (!silent) setLoading(true)
    try {
      const response = await getQualityProbeTargets({
        search: keyword.trim() || undefined,
        active: activeFilter === 'all' ? undefined : activeFilter === 'active',
      })
      const nextItems = response.items || []
      setItems(nextItems)
    } catch (error: any) {
      if (!silent) message.error(error?.response?.data?.detail || '获取质量探测目标失败')
      setItems([])
    } finally {
      if (!silent) setLoading(false)
    }
  }

  const fetchDatacenters = async () => {
    try {
      setDatacenters(await getDatacenters())
    } catch {
      setDatacenters([])
    }
  }

  const fetchCircuits = async () => {
    try {
      const response = await getCircuits({ status: 'active', limit: 1000 })
      setCircuits(response.items || [])
    } catch {
      setCircuits([])
    }
  }

  const fetchNqaDevices = async (search = '') => {
    setNqaDevicesLoading(true)
    try {
      const response = await getDevices({
        limit: 100,
        status: 'active',
        is_monitored: true,
        search: search.trim() || undefined,
      })
      setNqaDevices((response.items || []).filter((item) => (item.monitor_source || 'snmp') === 'snmp'))
    } catch {
      setNqaDevices([])
    } finally {
      setNqaDevicesLoading(false)
    }
  }

  const fetchNqaInstances = async (deviceId?: number, forceRefresh = false) => {
    if (!deviceId) {
      setNqaInstances([])
      return
    }
    setNqaInstancesLoading(true)
    try {
      const response = await getQualityNqaInstances(deviceId, forceRefresh)
      setNqaInstances(response.items || [])
      if (!(response.items || []).length) message.info('该设备没有识别到可用的NQA/SNMP实例')
    } catch (error: any) {
      setNqaInstances([])
      message.error(error?.response?.data?.detail || '读取设备NQA实例失败')
    } finally {
      setNqaInstancesLoading(false)
    }
  }

  useEffect(() => {
    void fetchDatacenters()
    void fetchCircuits()
    void fetchNqaDevices()
  }, [])

  useEffect(() => {
    void fetchItems()
  }, [activeFilter])

  useEffect(() => {
    const timer = window.setInterval(() => {
      void fetchItems(true)
    }, 10000)
    return () => window.clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeFilter, keyword])

  const datacenterOptions = useMemo(
    () => datacenters.filter((item) => item.is_active !== false).map((item) => ({ label: item.name, value: item.id })),
    [datacenters]
  )
  const circuitOptions = useMemo(
    () => circuits
      .filter((circuit) => circuit.line_type === (probeSource === 'device_nqa_snmp' ? 'private_line' : 'internet'))
      .map((circuit) => ({
      value: circuit.id,
      label: [circuit.datacenter_name, circuit.name, circuit.customer_name || circuit.operator_name, circuit.primary_device_ip, circuit.primary_port_name]
        .filter(Boolean)
        .join(' / '),
    })),
    [circuits, probeSource]
  )

  const summaryItems = useMemo(() => items.filter((item) => item.is_active), [items])
  const slaItems = useMemo(
    () => [...summaryItems].sort((left, right) => {
      const leftLevel = qualityHealthStyles[getQualityHealthLevel(left)]
      const rightLevel = qualityHealthStyles[getQualityHealthLevel(right)]
      if (leftLevel.rank !== rightLevel.rank) return leftLevel.rank - rightLevel.rank
      return (getQualityAvailability(left) ?? -1) - (getQualityAvailability(right) ?? -1)
    }),
    [summaryItems]
  )
  const internetSlaItems = useMemo(
    () => slaItems.filter((item) => (item.probe_source || 'server_icmp') === 'server_icmp'),
    [slaItems]
  )
  const privateSlaItems = useMemo(
    () => slaItems.filter((item) => item.probe_source === 'device_nqa_snmp'),
    [slaItems]
  )
  const expandedTarget = useMemo(
    () => items.find((item) => item.id === Number(expandedRowKeys[0])),
    [expandedRowKeys, items]
  )

  const openTargetChart = (record: QualityProbeTarget) => {
    setExpandedRowKeys((keys) => Number(keys[0]) === record.id ? [] : [record.id])
  }

  const openCreate = async () => {
    setEditing(null)
    setNqaInstances([])
    form.resetFields()
    form.setFieldsValue({
      probe_source: 'server_icmp',
      interval_seconds: 60,
      packet_count: 5,
      timeout_ms: 1000,
      latency_threshold_ms: 100,
      loss_threshold_percent: 1,
      jitter_threshold_ms: 30,
      is_active: true,
      alert_enabled: false,
      alert_loss_threshold_percent: 10,
      alert_consecutive_samples: 5,
      alert_webhook_url: '',
      alert_mention_users_text: '',
    })
    setModalOpen(true)
  }

  const openEdit = async (record: QualityProbeTarget) => {
    setEditing(record)
    form.resetFields()
    form.setFieldsValue({
      ...record,
      probe_source: record.probe_source || 'server_icmp',
      nqa_instance_key: record.probe_source === 'device_nqa_snmp'
        ? nqaInstanceValue(record.nqa_admin_name, record.nqa_operation_tag)
        : undefined,
      datacenter_id: record.datacenter_id || undefined,
      alert_enabled: false,
      alert_loss_threshold_percent: 10,
      alert_consecutive_samples: 5,
      alert_webhook_url: '',
      alert_mention_users_text: '',
    })
    setModalOpen(true)
    if (record.probe_source === 'device_nqa_snmp' && record.device_id) {
      void fetchNqaDevices(record.device_name || record.device_ip || '')
      void fetchNqaInstances(record.device_id)
    } else {
      setNqaInstances([])
    }
    setTargetAlertSettingsLoading(true)
    try {
      const settings = await getQualityTargetAlertSettings(record.id)
      form.setFieldsValue({
        alert_enabled: settings.enabled,
        alert_loss_threshold_percent: settings.loss_threshold_percent,
        alert_consecutive_samples: settings.consecutive_samples,
        alert_webhook_url: settings.webhook_url,
        alert_mention_users_text: (settings.mention_users || []).join(', '),
      })
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '读取该目标告警配置失败')
    } finally {
      setTargetAlertSettingsLoading(false)
    }
  }

  const handleSave = async () => {
    const values = await form.validateFields()
    setSaving(true)
    try {
      const {
        alert_enabled,
        alert_loss_threshold_percent,
        alert_consecutive_samples,
        alert_webhook_url,
        alert_mention_users_text,
        nqa_instance_key: _nqaInstanceKey,
        ...targetValues
      } = values
      let savedTarget: QualityProbeTarget
      if (editing) {
        savedTarget = await updateQualityProbeTarget(editing.id, targetValues)
      } else {
        savedTarget = await createQualityProbeTarget(targetValues)
      }
      await saveQualityTargetAlertSettings(savedTarget.id, {
        enabled: alert_enabled,
        loss_threshold_percent: alert_loss_threshold_percent,
        consecutive_samples: alert_consecutive_samples,
        webhook_url: alert_webhook_url,
        mention_users: String(alert_mention_users_text || '')
          .split(/[,，;；\s]+/)
          .map((item) => item.trim())
          .filter(Boolean),
      })
      message.success(editing ? '质量探测目标和告警配置已更新' : '质量探测目标和告警配置已添加')
      setModalOpen(false)
      setEditing(null)
      await fetchItems()
      setExpandedRowKeys([savedTarget.id])
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (record: QualityProbeTarget) => {
    try {
      await deleteQualityProbeTarget(record.id)
      message.success('已删除')
      await fetchItems()
      setExpandedRowKeys((keys) => keys.filter((key) => key !== record.id))
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '删除失败')
    }
  }

  const handleTest = async (record: QualityProbeTarget) => {
    setTestingId(record.id)
    try {
      const response = await testQualityProbeTarget(record.id)
      const result = response.result
      if (result.success) {
        message.success(`测试成功：平均 ${result.avg_latency_ms ?? '-'} ms，丢包 ${result.packet_loss_percent ?? '-'}%`)
      } else {
        message.warning(result.error || '测试未收到响应')
      }
      await fetchItems(true)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '测试失败')
    } finally {
      setTestingId(null)
    }
  }

  const handleMtr = async (record: QualityProbeTarget) => {
    setMtrLoadingId(record.id)
    setMtrTitle(`${record.name} / ${record.target}`)
    setMtrCommand('')
    setMtrOutput('')
    setMtrOpen(true)
    try {
      const result = await runQualityProbeMtr(record.id)
      setMtrCommand(result.command || result.tool || 'MTR')
      setMtrOutput(result.output || '无输出')
    } catch (error: any) {
      setMtrOutput(error?.response?.data?.detail || 'MTR 执行失败')
    } finally {
      setMtrLoadingId(null)
    }
  }

  const renderTargetActions = (record: QualityProbeTarget) => (
    <Space size={4} wrap>
      <Button size="small" onClick={() => openTargetChart(record)}>
        {Number(expandedRowKeys[0]) === record.id ? '收起' : '查看'}
      </Button>
      <Button
        size="small"
        icon={<ThunderboltOutlined />}
        loading={testingId === record.id}
        onClick={() => handleTest(record)}
      >
        立即测试
      </Button>
      <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(record)}>编辑</Button>
      <Button size="small" loading={mtrLoadingId === record.id} onClick={() => handleMtr(record)}>MTR</Button>
      <Popconfirm title="确认删除这个探测目标？" onConfirm={() => handleDelete(record)}>
        <Button size="small" danger icon={<DeleteOutlined />}>删除</Button>
      </Popconfirm>
    </Space>
  )

  const renderSlaCards = (records: QualityProbeTarget[], source: 'internet' | 'private') => (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(175px, 1fr))', gap: 10 }}>
      {records.map((record) => {
        const level = getQualityHealthLevel(record)
        const appearance = qualityHealthStyles[level]
        const availability = getQualityAvailability(record)
        const reason = getQualityHealthReason(record)
        const isExpanded = Number(expandedRowKeys[0]) === record.id
        const binding = source === 'private'
          ? [record.circuit_name || '未关联专线', record.circuit_device_ip || record.device_ip, record.circuit_port_name].filter(Boolean).join(' / ')
          : [record.circuit_name, record.operator_name].filter(Boolean).join(' / ')
        return (
          <Popover
            key={record.id}
            trigger="click"
            placement="bottomLeft"
            title={record.name}
            content={renderTargetActions(record)}
          >
            <div
              role="button"
              tabIndex={0}
              style={{
                minWidth: 0,
                padding: '10px 12px',
                border: `1px solid ${appearance.border}`,
                borderRadius: 7,
                background: appearance.background,
                cursor: 'pointer',
                boxShadow: isExpanded ? `0 0 0 2px ${source === 'internet' ? '#91caff' : '#d3adf7'}` : 'none',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 6, alignItems: 'center' }}>
                <Text strong ellipsis={{ tooltip: record.name }} style={{ minWidth: 0 }}>{record.name}</Text>
                <Tag color={source === 'internet' ? 'blue' : 'purple'} style={{ marginInlineEnd: 0, whiteSpace: 'nowrap' }}>
                  {source === 'internet' ? '公网ICMP' : '专线NQA'}
                </Tag>
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginTop: 5 }}>
                <span>
                  <Text strong style={{ color: appearance.color, fontSize: 22 }}>
                    {availability === null ? '-' : availability.toFixed(2)}
                  </Text>
                  {availability !== null ? <Text style={{ color: appearance.color }}> %</Text> : null}
                </span>
                <Text style={{ color: appearance.color, whiteSpace: 'nowrap' }}>{reason}</Text>
              </div>
              <Text type="secondary" style={{ display: 'block', fontSize: 12 }}>
                延迟 {record.last_avg_latency_ms == null ? '-' : `${Number(record.last_avg_latency_ms).toFixed(2)} ms`}
                {' · '}丢包 {record.last_packet_loss_percent == null ? '-' : `${Number(record.last_packet_loss_percent).toFixed(2)}%`}
              </Text>
              <Text
                type={source === 'private' && !record.circuit_name ? 'warning' : 'secondary'}
                ellipsis={{ tooltip: binding || record.target }}
                style={{ display: 'block', fontSize: 11, marginTop: 3 }}
              >
                {binding || record.target}
              </Text>
              {source === 'private' && (record.circuit_local_interconnect_ip || record.circuit_remote_interconnect_ip) ? (
                <Text
                  type="secondary"
                  ellipsis={{
                    tooltip: `本端 ${record.circuit_local_interconnect_ip || '-'} → 对端 ${record.circuit_remote_interconnect_ip || record.target || '-'}`,
                  }}
                  style={{ display: 'block', fontSize: 11, marginTop: 2 }}
                >
                  互联：{record.circuit_local_interconnect_ip || '-'} → {record.circuit_remote_interconnect_ip || record.target || '-'}
                </Text>
              ) : null}
              <Text type="secondary" style={{ display: 'block', fontSize: 11, marginTop: 4 }}>
                阈值 {record.latency_threshold_ms}ms / {record.loss_threshold_percent}%
              </Text>
            </div>
          </Popover>
        )
      })}
      {!records.length ? <Text type="secondary">当前筛选范围内暂无探测目标</Text> : null}
    </div>
  )

  return (
    <Space direction="vertical" size="middle" style={{ width: '100%' }}>
      <div style={{ color: '#8c8c8c', fontSize: 13 }}>监控中心 / 质量查询</div>
      <Card
        title={(
          <Space direction="vertical" size={0}>
            <Title level={4} style={{ margin: 0 }}>质量探测</Title>
            <Text type="secondary" style={{ fontSize: 12 }}>
              公网使用服务器 ICMP 探测，专线使用设备 NQA/SNMP；点击卡片后可选择查看、立即测试、编辑、MTR或删除。
            </Text>
          </Space>
        )}
        extra={(
          <Space wrap>
            <Button icon={<ReloadOutlined />} loading={loading} onClick={() => fetchItems()}>刷新</Button>
            <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新增目标</Button>
          </Space>
        )}
      >
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Space wrap>
            <Select
              value={activeFilter}
              style={{ width: 120 }}
              onChange={setActiveFilter}
              options={[
                { label: '全部状态', value: 'all' },
                { label: '启用', value: 'active' },
                { label: '停用', value: 'inactive' },
              ]}
            />
            <Input.Search
              allowClear
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              onSearch={() => fetchItems()}
              placeholder="搜索名称、目标、机房、运营商"
              style={{ width: 360 }}
            />
          </Space>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(520px, 1fr))', gap: 12, alignItems: 'start' }}>
            <Card
              size="small"
              title={<Space><Tag color="blue">服务器 ICMP</Tag><Text strong>互联网质量</Text></Space>}
              extra={<Text type="secondary">{internetSlaItems.length} 条</Text>}
              styles={{ body: { padding: 12, maxHeight: 430, overflowY: 'auto' } }}
            >
              {renderSlaCards(internetSlaItems, 'internet')}
            </Card>
            <Card
              size="small"
              title={<Space><Tag color="purple">设备 NQA / SNMP</Tag><Text strong>专线质量</Text></Space>}
              extra={<Text type="secondary">{privateSlaItems.length} 条</Text>}
              styles={{ body: { padding: 12, maxHeight: 430, overflowY: 'auto' } }}
            >
              {renderSlaCards(privateSlaItems, 'private')}
            </Card>
          </div>

          {expandedTarget ? (
            <Card
              size="small"
              title={`${expandedTarget.name} / ${expandedTarget.probe_source === 'device_nqa_snmp' ? '专线 NQA' : '公网 ICMP'}`}
              extra={<Button size="small" onClick={() => setExpandedRowKeys([])}>收起曲线</Button>}
            >
              <QualityChartPanel target={expandedTarget} />
            </Card>
          ) : null}
        </Space>
      </Card>

      <Modal
        title={editing ? '编辑质量探测目标' : '新增质量探测目标'}
        open={modalOpen}
        onOk={handleSave}
        confirmLoading={saving}
        onCancel={() => {
          setModalOpen(false)
          setEditing(null)
        }}
        width={820}
        destroyOnClose
      >
        <Spin spinning={targetAlertSettingsLoading}>
        <Form form={form} layout="vertical" preserve={false}>
          <Form.Item name="probe_source" label="探测数据源" rules={[{ required: true }]}>
            <Segmented
              options={[
                { label: '服务器 ICMP', value: 'server_icmp' },
                { label: '设备 NQA / SNMP', value: 'device_nqa_snmp' },
              ]}
              onChange={(value) => {
                form.setFieldValue('circuit_id', undefined)
                if (value === 'server_icmp') {
                  setNqaInstances([])
                  form.setFieldsValue({
                    device_id: undefined,
                    nqa_instance_key: undefined,
                    nqa_admin_name: undefined,
                    nqa_operation_tag: undefined,
                    target: undefined,
                    interval_seconds: 60,
                    packet_count: 5,
                    timeout_ms: 1000,
                  })
                } else {
                  form.setFieldsValue({ target: undefined, interval_seconds: 3 })
                  void fetchNqaDevices()
                }
              }}
            />
          </Form.Item>

          {probeSource === 'device_nqa_snmp' ? (
            <Card size="small" style={{ marginBottom: 16, background: '#f6ffed', borderColor: '#b7eb8f' }}>
              <Space size="middle" align="start" wrap>
                <Form.Item
                  name="device_id"
                  label="NQA采集设备"
                  rules={[{ required: true, message: '请选择配置了NQA的设备' }]}
                  style={{ width: 360, marginBottom: 8 }}
                >
                  <Select
                    showSearch
                    filterOption={false}
                    loading={nqaDevicesLoading}
                    placeholder="输入设备名称或管理IP搜索"
                    options={nqaDevices.map((device) => ({
                      label: `${device.name}（${device.ip_address}）`,
                      value: device.id,
                    }))}
                    onSearch={(value) => {
                      if (nqaDeviceSearchTimer.current) window.clearTimeout(nqaDeviceSearchTimer.current)
                      nqaDeviceSearchTimer.current = window.setTimeout(() => void fetchNqaDevices(value), 300)
                    }}
                    onChange={(deviceId) => {
                      setNqaInstances([])
                      form.setFieldsValue({
                        nqa_instance_key: undefined,
                        nqa_admin_name: undefined,
                        nqa_operation_tag: undefined,
                        target: undefined,
                      })
                      void fetchNqaInstances(deviceId)
                    }}
                  />
                </Form.Item>
                <Form.Item
                  name="nqa_instance_key"
                  label="已识别的NQA实例"
                  rules={[{ required: true, message: '请选择设备上已识别的NQA实例' }]}
                  style={{ width: 330, marginBottom: 8 }}
                >
                  <Select
                    loading={nqaInstancesLoading}
                    disabled={!selectedNqaDeviceId}
                    placeholder="选择后自动带出目标地址"
                    notFoundContent={nqaInstancesLoading ? <Spin size="small" /> : '未识别到NQA实例'}
                    options={nqaInstances.map((instance) => {
                      const existing = items.find((item) => (
                        item.probe_source === 'device_nqa_snmp'
                        && item.device_id === selectedNqaDeviceId
                        && item.nqa_admin_name === instance.admin_name
                        && item.nqa_operation_tag === instance.operation_tag
                        && item.id !== editing?.id
                      ))
                      return {
                        value: nqaInstanceValue(instance.admin_name, instance.operation_tag),
                        disabled: Boolean(existing),
                        label: `${instance.admin_name}/${instance.operation_tag} → ${instance.target || '未知目标'}${instance.has_result ? `（${instance.avg_latency_ms ?? '-'} ms / 丢包${instance.packet_loss_percent ?? '-'}%）` : '（暂无结果）'}${existing ? ` · 已生成图：${existing.name}` : ''}`,
                      }
                    })}
                    onChange={(value) => {
                      const instance = nqaInstances.find((item) => nqaInstanceValue(item.admin_name, item.operation_tag) === value)
                      const device = nqaDevices.find((item) => item.id === form.getFieldValue('device_id'))
                      if (!instance) return
                      const targetAddress = String(instance.target || '').trim()
                      const privateCircuits = circuits.filter((item) => item.line_type === 'private_line')
                      const addressFields = (circuit: Circuit) => [
                        circuit.primary_remote_interconnect_ip,
                        circuit.secondary_remote_interconnect_ip,
                        circuit.remote_interconnect_address,
                        circuit.primary_interconnect_ip,
                        circuit.secondary_interconnect_ip,
                        circuit.interconnect_address,
                      ].map((item) => String(item || '').trim()).filter(Boolean)
                      const exactCircuit = privateCircuits.find((circuit) => (
                        [circuit.primary_device_id, circuit.secondary_device_id, circuit.aggregation_monitor_device_id].includes(device?.id)
                        && addressFields(circuit).some((address) => address === targetAddress || address.split('/')[0] === targetAddress)
                      ))
                      const deviceCircuit = privateCircuits.find((circuit) => (
                        [circuit.primary_device_id, circuit.secondary_device_id, circuit.aggregation_monitor_device_id].includes(device?.id)
                      ))
                      const matchedCircuit = exactCircuit || deviceCircuit
                      form.setFieldsValue({
                        name: form.getFieldValue('name') || `${device?.name || '设备'} NQA`,
                        target: instance.target,
                        nqa_admin_name: instance.admin_name,
                        nqa_operation_tag: instance.operation_tag,
                        interval_seconds: instance.frequency_seconds || 3,
                        packet_count: instance.packet_count || 1,
                        timeout_ms: instance.timeout_ms || 1000,
                        circuit_id: matchedCircuit?.id,
                        datacenter_id: device?.datacenter_id || form.getFieldValue('datacenter_id'),
                        operator_name: matchedCircuit?.operator_name || form.getFieldValue('operator_name'),
                        description: form.getFieldValue('description') || `设备NQA ${instance.admin_name}/${instance.operation_tag}${instance.source ? `，源地址 ${instance.source}` : ''}`,
                      })
                    }}
                  />
                </Form.Item>
                <Button
                  style={{ marginTop: 30 }}
                  icon={<ReloadOutlined />}
                  loading={nqaInstancesLoading}
                  disabled={!selectedNqaDeviceId}
                  onClick={() => void fetchNqaInstances(selectedNqaDeviceId, true)}
                >
                  重新读取
                </Button>
              </Space>
              <Text type="secondary">只列出设备已经配置并能通过SNMP识别的NQA任务，系统不会从服务器直接 Ping 该目标。</Text>
              <Form.Item name="nqa_admin_name" hidden><Input /></Form.Item>
              <Form.Item name="nqa_operation_tag" hidden><Input /></Form.Item>
            </Card>
          ) : null}

          <Space size="middle" style={{ width: '100%' }} align="start">
            <Form.Item
              name="name"
              label="探测名称"
              rules={[{ required: true, message: '请输入探测名称' }]}
              style={{ width: 330 }}
            >
              <Input placeholder="例如：湖北宜昌-电信DNS" />
            </Form.Item>
            <Form.Item
              name="target"
              label="目标 IP / 域名"
              rules={[{ required: true, message: '请输入目标 IP 或域名' }]}
              style={{ width: 330 }}
            >
              <Input
                readOnly={probeSource === 'device_nqa_snmp'}
                placeholder={probeSource === 'device_nqa_snmp' ? '选择NQA实例后自动带出' : '例如：114.114.114.114 或 www.example.com'}
              />
            </Form.Item>
          </Space>

          <Space size="middle" style={{ width: '100%' }} align="start">
            <Form.Item name="datacenter_id" label="机房" style={{ width: 220 }}>
              <Select allowClear showSearch optionFilterProp="label" placeholder="选择机房" options={datacenterOptions} />
            </Form.Item>
            <Form.Item name="operator_name" label="运营商" style={{ width: 180 }}>
              <Input placeholder="电信/联通/移动/BGP" />
            </Form.Item>
            <Form.Item name="is_active" label="是否启用" valuePropName="checked" style={{ width: 120 }}>
              <Switch checkedChildren="启用" unCheckedChildren="停用" />
            </Form.Item>
          </Space>

          <Form.Item
            name="circuit_id"
            label={probeSource === 'device_nqa_snmp' ? '关联专线' : '关联公网线路'}
            extra={probeSource === 'device_nqa_snmp'
              ? '关联专线管理中已启用的线路，用于标识NQA所在设备、接口及互联地址。选择NQA实例时会优先自动匹配。'
              : '关联后会在质量曲线下同步展示该公网出口的流量，并辅助判断是否存在带宽拥塞。'}
          >
            <Select
              allowClear
              showSearch
              optionFilterProp="label"
              placeholder={probeSource === 'device_nqa_snmp' ? '选择专线管理中已启用的线路' : '选择公网管理中已启用的线路'}
              options={circuitOptions}
              onChange={(value) => {
                const circuit = circuits.find((item) => item.id === value)
                if (circuit) {
                  form.setFieldsValue({
                    circuit_id: value,
                    datacenter_id: circuit.datacenter_id || form.getFieldValue('datacenter_id'),
                    operator_name: circuit.operator_name || form.getFieldValue('operator_name'),
                  })
                }
              }}
            />
          </Form.Item>

          <Space size="middle" style={{ width: '100%' }} align="start">
            <Form.Item name="interval_seconds" label={probeSource === 'device_nqa_snmp' ? '系统读取间隔(s)' : '采样间隔(s)'} rules={[{ required: true }]} style={{ width: 150 }}>
              <InputNumber min={1} max={3600} style={{ width: '100%' }} />
            </Form.Item>
            {probeSource === 'server_icmp' ? (
              <>
                <Form.Item name="packet_count" label="每次包数" rules={[{ required: true }]} style={{ width: 150 }}>
                  <InputNumber min={1} max={20} style={{ width: '100%' }} />
                </Form.Item>
                <Form.Item name="timeout_ms" label="超时(ms)" rules={[{ required: true }]} style={{ width: 150 }}>
                  <InputNumber min={200} max={10000} style={{ width: '100%' }} />
                </Form.Item>
              </>
            ) : null}
            {probeSource === 'device_nqa_snmp' ? (
              <>
                <Form.Item name="packet_count" hidden><InputNumber /></Form.Item>
                <Form.Item name="timeout_ms" hidden><InputNumber /></Form.Item>
              </>
            ) : null}
          </Space>

          <Space size="middle" style={{ width: '100%' }} align="start">
            <Form.Item name="latency_threshold_ms" label="延迟阈值(ms)" rules={[{ required: true }]} style={{ width: 150 }}>
              <InputNumber min={1} max={10000} style={{ width: '100%' }} />
            </Form.Item>
            <Form.Item name="loss_threshold_percent" label="丢包阈值(%)" rules={[{ required: true }]} style={{ width: 150 }}>
              <InputNumber min={0} max={100} style={{ width: '100%' }} />
            </Form.Item>
          </Space>

          <Form.Item name="description" label="备注">
            <Input.TextArea rows={3} placeholder="可填写用途、运营商、线路背景等信息" />
          </Form.Item>

          <Card size="small" title="告警设置" style={{ background: '#f8fafc' }}>
            <Space size="middle" align="start" wrap>
              <Form.Item name="alert_enabled" label="启用该目标告警" valuePropName="checked" style={{ width: 140 }}>
                <Switch checkedChildren="启用" unCheckedChildren="停用" />
              </Form.Item>
              <Form.Item name="alert_consecutive_samples" label="连续丢包周期" rules={[{ required: true }]} style={{ width: 170 }}>
                <InputNumber min={1} max={60} precision={0} style={{ width: '100%' }} />
              </Form.Item>
              <Form.Item name="alert_loss_threshold_percent" label="5分钟丢包率阈值(%)" rules={[{ required: true }]} style={{ width: 210 }}>
                <InputNumber min={0.01} max={100} step={0.1} style={{ width: '100%' }} />
              </Form.Item>
            </Space>
            <Form.Item
              name="alert_webhook_url"
              label="该目标机器人 Webhook"
              dependencies={['alert_enabled']}
              rules={[
                ({ getFieldValue }) => ({
                  required: Boolean(getFieldValue('alert_enabled')),
                  message: '启用告警时必须填写机器人 Webhook',
                }),
                { type: 'url', message: 'Webhook 地址格式不正确' },
              ]}
            >
              <Input placeholder="支持企业微信、飞书、钉钉和通用 Webhook" />
            </Form.Item>
            <Form.Item name="alert_mention_users_text" label="该目标 @负责人" extra="多个对象用逗号分隔，企业微信支持手机号或 @all。">
              <Input placeholder="例如：13800138000, 13900139000" />
            </Form.Item>
            <Button loading={targetAlertTesting} onClick={testTargetAlertRobot}>发送测试消息</Button>
          </Card>
        </Form>
        </Spin>
      </Modal>
      <Modal
        title={`MTR - ${mtrTitle || '探测目标'}`}
        open={mtrOpen}
        width={900}
        footer={<Button onClick={() => setMtrOpen(false)}>关闭</Button>}
        onCancel={() => setMtrOpen(false)}
      >
        {mtrCommand ? <Text type="secondary">{mtrCommand}</Text> : null}
        <pre style={{
          marginTop: 12,
          padding: 12,
          minHeight: 260,
          maxHeight: 520,
          overflow: 'auto',
          borderRadius: 8,
          background: '#0f172a',
          color: '#d1e7ff',
          whiteSpace: 'pre-wrap',
        }}
        >
          {mtrOutput || (mtrLoadingId ? 'MTR 执行中...' : '暂无输出')}
        </pre>
      </Modal>
    </Space>
  )
}

export default QualityQuery
