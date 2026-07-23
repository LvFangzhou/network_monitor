import {
  DeleteOutlined,
  FullscreenExitOutlined,
  FullscreenOutlined,
  PlusOutlined,
} from '@ant-design/icons'
import { AutoComplete, Button, Card, Empty, Select, Space, Spin, Tag, Typography, message } from 'antd'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'

import {
  getMonitorDeviceInterfaces,
  MonitorDevice,
  MonitorDeviceSearchOption,
  MonitorInterface,
  searchMonitorDevices,
} from '../../api/metrics'

const { Text } = Typography

const GRAFANA_WARMUP_URL = '/grafana-app/d/network-interface-overview/network-interface-overview?orgId=1&from=now-5m&to=now&theme=light&viewPanel=1&var-device_name=__warmup__&var-device_ip=__warmup__&var-interface_name=__warmup__&kiosk'

interface GrafanaTarget {
  key: string
  device: MonitorDevice
  interface: MonitorInterface
  metricKey: string
  metricLabel: string
  panelId: number
  timeFrom: string
  timeTo: string
  refreshInterval: string
  reloadKey: number
}

const MONITOR_OPTIONS = [
  { value: 'traffic', label: '接口流量', panelId: 1 },
  { value: 'utilization', label: '接口利用率', panelId: 2 },
  { value: 'discards', label: '接口丢弃包增量', panelId: 3 },
  { value: 'errors', label: '接口错误包增量', panelId: 4 },
  { value: 'pfc', label: 'PFC 收发包增量', panelId: 5 },
  { value: 'ecn', label: 'ECN 标记包增量', panelId: 6 },
]

const statusRank = (item: MonitorInterface) => (String(item.oper_status).toLowerCase() === 'up' ? 0 : 1)

const normalizeInterfaceName = (value?: string | null) => String(value || '')
  .trim()
  .toLowerCase()
  .replace(/fourhundredgigabitethernet|fourhundredgige|fhgigabitethernet|fhgige|400ge/g, '400ge')
  .replace(/hundredgigabitethernet|hundredgige|100ge/g, '100ge')
  .replace(/twentyfivegigabitethernet|twentyfivegige|25ge/g, '25ge')
  .replace(/ten-gigabitethernet|tengigabitethernet|ten-gige|10ge/g, '10ge')
  .replace(/gigabitethernet|gigabitethernet|gige/g, 'ge')
  .replace(/[\s._-]+/g, '')

type CircuitMonitorTarget = {
  deviceId: number
  deviceIp?: string
  deviceName?: string
  portName: string
}

const buildPanelUrl = (target: GrafanaTarget) => {
  const params = new URLSearchParams({
    orgId: '1',
    from: target.timeFrom,
    to: target.timeTo,
    refresh: target.refreshInterval,
    theme: 'light',
    'var-device_name': target.device.name,
    'var-device_ip': target.device.ip_address,
    'var-interface_name': target.interface.name,
    viewPanel: String(target.panelId),
    _: String(target.reloadKey),
  })
  return `/grafana-app/d/network-interface-overview/network-interface-overview?${params.toString()}&kiosk`
}

const GrafanaDashboard = () => {
  const location = useLocation()
  const navigate = useNavigate()
  const [deviceKeyword, setDeviceKeyword] = useState('')
  const [deviceOptions, setDeviceOptions] = useState<MonitorDeviceSearchOption[]>([])
  const [selectedDevice, setSelectedDevice] = useState<MonitorDevice | null>(null)
  const [interfaces, setInterfaces] = useState<MonitorInterface[]>([])
  const [selectedInterfaceIndex, setSelectedInterfaceIndex] = useState<number | null>(null)
  const [selectedMetricKey, setSelectedMetricKey] = useState('traffic')
  const [targets, setTargets] = useState<GrafanaTarget[]>([])
  const [loadingDevices, setLoadingDevices] = useState(false)
  const [loadingInterfaces, setLoadingInterfaces] = useState(false)
  const [expandedTargetKey, setExpandedTargetKey] = useState<string | null>(null)
  const [grafanaWarmupPending, setGrafanaWarmupPending] = useState(true)
  const iframeRefs = useRef(new Map<string, HTMLIFrameElement>())
  const routeTargetsHandledRef = useRef(false)

  const sortedInterfaces = useMemo(
    () => [...interfaces].sort((a, b) => statusRank(a) - statusRank(b) || a.index - b.index),
    [interfaces],
  )

  useEffect(() => {
    const keyword = deviceKeyword.trim()
    if (!keyword || selectedDevice && keyword === `${selectedDevice.ip_address} / ${selectedDevice.name}`) {
      setDeviceOptions([])
      return
    }
    const timer = window.setTimeout(async () => {
      setLoadingDevices(true)
      try {
        setDeviceOptions(await searchMonitorDevices(keyword))
      } catch (error: any) {
        setDeviceOptions([])
        message.error(error?.response?.data?.detail || '设备搜索失败')
      } finally {
        setLoadingDevices(false)
      }
    }, 300)
    return () => window.clearTimeout(timer)
  }, [deviceKeyword, selectedDevice])

  useEffect(() => {
    const routeTargets = (location.state?.circuitMonitorTargets || []) as CircuitMonitorTarget[]
    if (!routeTargets.length || routeTargetsHandledRef.current) return
    routeTargetsHandledRef.current = true

    const loadRouteTargets = async () => {
      const metric = MONITOR_OPTIONS.find((item) => item.value === selectedMetricKey) || MONITOR_OPTIONS[0]
      const nextTargets: GrafanaTarget[] = []
      const missing: string[] = []
      const byDevice = new Map<number, CircuitMonitorTarget[]>()
      routeTargets.forEach((item) => byDevice.set(item.deviceId, [...(byDevice.get(item.deviceId) || []), item]))

      for (const [deviceId, requestedTargets] of byDevice.entries()) {
        try {
          const response = await getMonitorDeviceInterfaces(deviceId)
          requestedTargets.forEach((requested) => {
            const requestedKey = normalizeInterfaceName(requested.portName)
            const matched = response.interfaces.find((item) => [item.name, item.alias, item.description]
              .some((value) => normalizeInterfaceName(value) === requestedKey))
            if (!matched) {
              missing.push(`${requested.deviceIp || response.device.ip_address} / ${requested.portName}`)
              return
            }
            nextTargets.push({
              key: `${response.device.id}:${matched.index}`,
              device: response.device,
              interface: matched,
              metricKey: metric.value,
              metricLabel: metric.label,
              panelId: metric.panelId,
              timeFrom: 'now-6h',
              timeTo: 'now',
              refreshInterval: '30s',
              reloadKey: Date.now() + nextTargets.length,
            })
          })
        } catch {
          requestedTargets.forEach((item) => missing.push(`${item.deviceIp || item.deviceName || deviceId} / ${item.portName}`))
        }
      }

      if (nextTargets.length) {
        setTargets((current) => {
          const existing = new Set(current.map((item) => item.key))
          return [...current, ...nextTargets.filter((item) => !existing.has(item.key))]
        })
        setSelectedDevice(nextTargets[0].device)
        setDeviceKeyword(`${nextTargets[0].device.ip_address} / ${nextTargets[0].device.name}`)
        setInterfaces([])
        setSelectedInterfaceIndex(null)
      }
      if (missing.length) message.warning(`以下线路接口未识别：${missing.join('；')}`)
      navigate('/grafana', { replace: true, state: null })
    }

    void loadRouteTargets()
  }, [location.state, navigate, selectedMetricKey])

  const selectDevice = async (deviceId: number) => {
    const option = deviceOptions.find((item) => item.id === deviceId)
    if (option) setDeviceKeyword(`${option.ip_address} / ${option.name}`)
    setLoadingInterfaces(true)
    setSelectedInterfaceIndex(null)
    setInterfaces([])
    try {
      const response = await getMonitorDeviceInterfaces(deviceId)
      const sorted = [...response.interfaces].sort((a, b) => statusRank(a) - statusRank(b) || a.index - b.index)
      setSelectedDevice(response.device)
      setDeviceKeyword(`${response.device.ip_address} / ${response.device.name}`)
      setInterfaces(response.interfaces)
      setSelectedInterfaceIndex(sorted[0]?.index ?? null)
    } catch (error: any) {
      setSelectedDevice(null)
      message.error(error?.response?.data?.detail || '读取接口信息失败')
    } finally {
      setLoadingInterfaces(false)
    }
  }

  const addTarget = () => {
    if (!selectedDevice || selectedInterfaceIndex === null) {
      message.warning('请先选择设备和接口')
      return
    }
    const selectedInterface = interfaces.find((item) => item.index === selectedInterfaceIndex)
    if (!selectedInterface) return
    const metric = MONITOR_OPTIONS.find((item) => item.value === selectedMetricKey) || MONITOR_OPTIONS[0]
    const key = `${selectedDevice.id}:${selectedInterface.index}`
    if (targets.some((item) => item.key === key)) {
      message.info('该接口已经添加')
      return
    }
    setTargets((current) => [
      ...current,
      {
        key,
        device: selectedDevice,
        interface: selectedInterface,
        metricKey: metric.value,
        metricLabel: metric.label,
        panelId: metric.panelId,
        timeFrom: 'now-6h',
        timeTo: 'now',
        refreshInterval: '30s',
        reloadKey: Date.now(),
      },
    ])
  }

  const changeMonitorMetric = (metricKey: string) => {
    const metric = MONITOR_OPTIONS.find((item) => item.value === metricKey) || MONITOR_OPTIONS[0]
    const now = Date.now()
    setSelectedMetricKey(metric.value)
    setTargets((current) => current.map((target, index) => {
      let timeFrom = target.timeFrom
      let timeTo = target.timeTo
      try {
        const frameUrl = iframeRefs.current.get(target.key)?.contentWindow?.location.href
        if (frameUrl) {
          const params = new URL(frameUrl).searchParams
          timeFrom = params.get('from') || timeFrom
          timeTo = params.get('to') || timeTo
        }
      } catch {
        // 同源代理正常时可读取当前缩放范围；读取失败则保留上一次范围。
      }
      return {
        ...target,
        metricKey: metric.value,
        metricLabel: metric.label,
        panelId: metric.panelId,
        timeFrom,
        timeTo,
        reloadKey: now + index,
      }
    }))
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, minHeight: 'calc(100vh - 116px)' }}>
      {grafanaWarmupPending ? (
        <iframe
          aria-hidden="true"
          title="Grafana 资源预热"
          src={GRAFANA_WARMUP_URL}
          onLoad={() => setGrafanaWarmupPending(false)}
          style={{ position: 'absolute', width: 1, height: 1, border: 0, opacity: 0, pointerEvents: 'none' }}
        />
      ) : null}

      <Card size="small" styles={{ body: { padding: 12 } }}>
        <Space.Compact style={{ width: '100%', maxWidth: 1320 }}>
          <AutoComplete
            value={deviceKeyword}
            options={deviceOptions.map((item) => ({
              value: String(item.id),
              label: `${item.ip_address} / ${item.name}`,
            }))}
            onSearch={(value) => {
              setDeviceKeyword(value)
              if (selectedDevice && value !== `${selectedDevice.ip_address} / ${selectedDevice.name}`) {
                setSelectedDevice(null)
                setInterfaces([])
                setSelectedInterfaceIndex(null)
              }
            }}
            onSelect={(value) => void selectDevice(Number(value))}
            placeholder="输入设备名称或管理 IP"
            notFoundContent={loadingDevices ? <Spin size="small" /> : '未匹配到设备'}
            style={{ width: 360 }}
          />
          <Select
            showSearch
            value={selectedInterfaceIndex}
            loading={loadingInterfaces}
            disabled={!selectedDevice || loadingInterfaces}
            placeholder={selectedDevice ? '选择接口' : '请先选择设备'}
            optionFilterProp="label"
            onChange={setSelectedInterfaceIndex}
            options={sortedInterfaces.map((item) => ({
              value: item.index,
              label: item.name,
              status: String(item.oper_status || 'unknown').toLowerCase(),
              adminStatus: String(item.admin_status || 'unknown').toLowerCase(),
            }))}
            optionRender={(option) => {
              const status = String(option.data.status)
              const adminStatus = String(option.data.adminStatus)
              return (
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                  <span>{String(option.label)}</span>
                  <Space size={4}>
                    <Tag color={status === 'up' ? 'success' : 'default'} style={{ marginInlineEnd: 0 }}>
                      {status === 'up' ? 'UP' : 'DOWN'}
                    </Tag>
                    {adminStatus === 'down' ? <Tag style={{ marginInlineEnd: 0 }}>管理DOWN</Tag> : null}
                  </Space>
                </div>
              )
            }}
            style={{ width: 390 }}
          />
          <Select
            value={selectedMetricKey}
            options={MONITOR_OPTIONS}
            onChange={changeMonitorMetric}
            style={{ width: 220 }}
          />
          <Button type="primary" icon={<PlusOutlined />} onClick={addTarget} disabled={!selectedDevice || selectedInterfaceIndex === null}>
            添加图表
          </Button>
        </Space.Compact>
        {selectedDevice ? (
          <Text type="secondary" style={{ display: 'block', marginTop: 8 }}>
            已识别 {interfaces.length} 个接口，列表按 UP、DOWN 排序；切换监控项会同步更新全部已添加图表。
          </Text>
        ) : null}
      </Card>

      {targets.length === 0 ? (
        <Card><Empty description="请选择设备和接口，然后点击“添加图表”" /></Card>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 12 }}>
          {targets.map((target) => {
            const expanded = expandedTargetKey === target.key
            return (
            <Card
              key={target.key}
              size="small"
              extra={(
                <Space size={6} wrap={false}>
                  {!expanded ? (
                    <Tag color={String(target.interface.oper_status).toLowerCase() === 'up' ? 'success' : 'default'} style={{ marginInlineEnd: 0 }}>
                      {String(target.interface.oper_status).toLowerCase() === 'up' ? 'UP' : 'DOWN'}
                    </Tag>
                  ) : null}
                  <Button
                    size="small"
                    title={expanded ? '退出全屏' : '当前页面全屏'}
                    icon={expanded ? <FullscreenExitOutlined /> : <FullscreenOutlined />}
                    onClick={() => setExpandedTargetKey(expanded ? null : target.key)}
                  />
                  {!expanded ? (
                    <Button
                      size="small"
                      danger
                      icon={<DeleteOutlined />}
                      onClick={() => setTargets((current) => current.filter((item) => item.key !== target.key))}
                    />
                  ) : null}
                </Space>
              )}
              style={expanded ? {
                position: 'fixed',
                top: 58,
                right: 12,
                bottom: 12,
                left: 12,
                zIndex: 1200,
                boxShadow: '0 12px 36px rgba(0, 0, 0, 0.24)',
              } : undefined}
              styles={{
                header: { minHeight: 40 },
                body: { padding: 0, height: expanded ? 'calc(100vh - 116px)' : 470, overflow: 'hidden', position: 'relative' },
              }}
            >
              <iframe
                key={target.reloadKey}
                ref={(element) => {
                  if (element) iframeRefs.current.set(target.key, element)
                  else iframeRefs.current.delete(target.key)
                }}
                title={`${target.device.ip_address} / ${target.interface.name}`}
                src={buildPanelUrl(target)}
                style={{ width: '100%', height: '100%', border: 0, display: 'block' }}
                allowFullScreen
              />
            </Card>
            )
          })}
        </div>
      )}
    </div>
  )
}

export default GrafanaDashboard
