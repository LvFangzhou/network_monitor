import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip as ChartTooltip, XAxis, YAxis } from 'recharts'

type TrafficPoint = {
  time: string
  in_bps: number
  out_bps: number
}

type MetricPoint = {
  time: string
  value: number
}

type DiscardPoint = {
  time: string
  in_discards_delta: number
  out_discards_delta: number
}

export const InterfaceTrafficChart = ({ data, formatBps }: { data: TrafficPoint[]; formatBps: (value?: number | null) => string }) => (
  <ResponsiveContainer width="100%" height={220}>
    <LineChart data={data} margin={{ top: 12, right: 18, left: 18, bottom: 8 }}>
      <CartesianGrid strokeDasharray="3 3" />
      <XAxis dataKey="time" minTickGap={28} tick={{ fontSize: 11 }} />
      <YAxis tickFormatter={formatBps} width={86} tick={{ fontSize: 11 }} />
      <ChartTooltip formatter={(value) => formatBps(Number(value))} />
      <Line type="monotone" dataKey="in_bps" name="In" stroke="#52c41a" dot={false} strokeWidth={1.8} />
      <Line type="monotone" dataKey="out_bps" name="Out" stroke="#1677ff" dot={false} strokeWidth={1.8} />
    </LineChart>
  </ResponsiveContainer>
)

export const MetricTrendChart = ({
  data,
  unit,
  formatMetricValue,
}: {
  data: MetricPoint[]
  unit: string
  formatMetricValue: (value: any, unit?: string) => string
}) => (
  <ResponsiveContainer width="100%" height={260}>
    <LineChart data={data}>
      <CartesianGrid strokeDasharray="3 3" />
      <XAxis dataKey="time" minTickGap={28} />
      <YAxis tickFormatter={(value) => formatMetricValue(value, unit)} />
      <ChartTooltip formatter={(value) => formatMetricValue(value, unit)} />
      <Line type="monotone" dataKey="value" stroke="#f5222d" dot={false} strokeWidth={1.8} />
    </LineChart>
  </ResponsiveContainer>
)

export const InterfaceDiscardChart = ({ data }: { data: DiscardPoint[] }) => (
  <ResponsiveContainer width="100%" height={240}>
    <LineChart data={data} margin={{ top: 12, right: 18, left: 18, bottom: 8 }}>
      <CartesianGrid strokeDasharray="3 3" />
      <XAxis dataKey="time" minTickGap={28} tick={{ fontSize: 11 }} />
      <YAxis tickFormatter={(value) => `${Number(value || 0).toFixed(0)}包`} width={76} tick={{ fontSize: 11 }} />
      <ChartTooltip formatter={(value) => `${Number(value || 0).toFixed(0)}包`} />
      <Line type="monotone" dataKey="in_discards_delta" name="入向丢弃" stroke="#fa8c16" dot={false} strokeWidth={1.8} connectNulls />
      <Line type="monotone" dataKey="out_discards_delta" name="出向丢弃" stroke="#f5222d" dot={false} strokeWidth={1.8} connectNulls />
    </LineChart>
  </ResponsiveContainer>
)
