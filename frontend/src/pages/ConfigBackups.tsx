import { useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from 'react'
import { Button, Card, Col, Drawer, Form, Input, Progress, Row, Select, Space, Spin, Statistic, Table, Tag, Tooltip, Typography, message, theme } from 'antd'
import { CloudDownloadOutlined, EyeOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import {
  cancelConfigBackupJob,
  getConfigBackupFilters,
  getConfigBackupJob,
  getConfigBackupJobs,
  getConfigBackupSettings,
  getConfigBackupResult,
  getLatestConfigBackupJob,
  saveConfigBackupSettings,
  searchConfigBackups,
  testConfigBackupNotification,
  triggerConfigBackup,
  type ConfigBackupJob,
  type ConfigBackupResult,
  type ConfigSearchMatch,
} from '../api/configBackups'

const { Text, Paragraph } = Typography

const detectWebhookLabel = (url?: string) => {
  const value = (url || '').toLowerCase()
  if (!value) return '未填写'
  if (value.includes('work.weixin.qq.com') || value.includes('qyapi.weixin.qq.com')) return '企业微信'
  if (value.includes('oapi.dingtalk.com') || value.includes('api.dingtalk.com')) return '钉钉'
  if (value.includes('open.feishu.cn') || value.includes('open.larksuite.com')) return '飞书'
  return '通用 Webhook'
}

const statusColor: Record<string, string> = {
  pending: 'blue',
  running: 'processing',
  success: 'green',
  partial_failed: 'orange',
  failed: 'red',
  cancelled: 'default',
}

const statusLabel: Record<string, string> = {
  pending: '等待中',
  running: '备份中',
  success: '成功',
  partial_failed: '部分失败',
  failed: '失败',
  cancelled: '已取消',
}

const syncStatusMeta: Record<string, { label: string; color: string }> = {
  matched: { label: '一致', color: 'green' },
  changed_saved: { label: '不一致/已保存', color: 'orange' },
  changed_save_failed: { label: '不一致/保存失败', color: 'red' },
  check_failed: { label: '检查失败', color: 'red' },
  unsupported: { label: '暂不支持', color: 'default' },
}

const renderSyncStatus = (record: ConfigBackupResult) => {
  const meta = syncStatusMeta[record.config_sync_status || ''] || { label: '-', color: 'default' }
  const extra = record.config_save_message || record.config_sync_diff
  const tag = <Tag color={meta.color}>{meta.label}</Tag>
  return extra ? <Tooltip title={<pre style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{extra}</pre>}>{tag}</Tooltip> : tag
}

const formatTime = (value?: string | null) => value ? new Date(value).toLocaleString() : '-'

const maskWebhook = (value?: string) => {
  const text = (value || '').trim()
  if (!text) return '未配置'
  if (text.length <= 18) return `${text.slice(0, 4)}******${text.slice(-4)}`
  return `${text.slice(0, 12)}******${text.slice(-10)}`
}

const highlightText = (text: string, keyword: string) => {
  if (!keyword) return text
  const index = text.toLowerCase().indexOf(keyword.toLowerCase())
  if (index < 0) return text
  return (
    <>
      {text.slice(0, index)}
      <mark style={{ padding: '0 2px', borderRadius: 3, background: '#fde68a' }}>{text.slice(index, index + keyword.length)}</mark>
      {text.slice(index + keyword.length)}
    </>
  )
}

const parseDiffLineSets = (diff?: string | null) => {
  const added = new Set<string>()
  const removed = new Set<string>()
  for (const line of (diff || '').split('\n')) {
    if (line.startsWith('+++') || line.startsWith('---') || line.startsWith('@@')) continue
    if (line.startsWith('+')) added.add(line.slice(1).trim())
    if (line.startsWith('-')) removed.add(line.slice(1).trim())
  }
  return { added, removed }
}

const findFirstChangedLine = (content: string, changedLines: Set<string>) => {
  const lines = content.split('\n')
  for (let index = 0; index < lines.length; index += 1) {
    if (changedLines.has(lines[index].trim())) return index + 1
  }
  return null
}

type WidthMap = Record<string, number>
const uniqueFilterOptions = (values: Array<string | null | undefined>) =>
  Array.from(new Set(values.filter(Boolean) as string[])).sort().map((value) => ({ text: value, value }))

const ipToNumber = (value?: string) => {
  const parts = String(value || '').split('.').map((item) => Number(item))
  if (parts.length !== 4 || parts.some((item) => Number.isNaN(item))) return Number.MAX_SAFE_INTEGER
  return parts.reduce((total, item) => total * 256 + item, 0)
}

const ConfigBackups = () => {
  const {
    token: { colorBgContainer, colorFillQuaternary },
  } = theme.useToken()
  const [latestJob, setLatestJob] = useState<ConfigBackupJob | null>(null)
  const [jobs, setJobs] = useState<ConfigBackupJob[]>([])
  const [jobsTotal, setJobsTotal] = useState(0)
  const [jobsPage, setJobsPage] = useState(1)
  const [jobsPageSize, setJobsPageSize] = useState(10)
  const [jobsLoading, setJobsLoading] = useState(false)
  const [triggering, setTriggering] = useState(false)
  const [keyword, setKeyword] = useState('')
  const [datacenter, setDatacenter] = useState<string>()
  const [deviceIp, setDeviceIp] = useState('')
  const [datacenters, setDatacenters] = useState<Array<{ name: string }>>([])
  const [searchLoading, setSearchLoading] = useState(false)
  const [searchItems, setSearchItems] = useState<ConfigSearchMatch[]>([])
  const [searchJob, setSearchJob] = useState<ConfigBackupJob | null>(null)
  const [resultDrawerOpen, setResultDrawerOpen] = useState(false)
  const [resultLoading, setResultLoading] = useState(false)
  const [resultDetail, setResultDetail] = useState<ConfigBackupResult | null>(null)
  const [highlightLine, setHighlightLine] = useState<number | null>(null)
  const [highlightStartupLine, setHighlightStartupLine] = useState<number | null>(null)
  const [jobResultsOpen, setJobResultsOpen] = useState(false)
  const [jobResultsLoading, setJobResultsLoading] = useState(false)
  const [jobResults, setJobResults] = useState<ConfigBackupResult[]>([])
  const [latestResults, setLatestResults] = useState<ConfigBackupResult[]>([])
  const [latestResultsJobId, setLatestResultsJobId] = useState<number | null>(null)
  const [latestResultsLoading, setLatestResultsLoading] = useState(false)
  const [resultFilterText, setResultFilterText] = useState('')
  const [resultDatacenterFilter, setResultDatacenterFilter] = useState<string>()
  const [resultVendorFilter, setResultVendorFilter] = useState<string>()
  const [resultDeviceTypeFilter, setResultDeviceTypeFilter] = useState<string>()
  const [notificationWebhook, setNotificationWebhook] = useState('')
  const [savedNotificationWebhook, setSavedNotificationWebhook] = useState('')
  const [webhookEditing, setWebhookEditing] = useState(false)
  const [webhookLabel, setWebhookLabel] = useState('未填写')
  const [savingWebhook, setSavingWebhook] = useState(false)
  const [testingWebhook, setTestingWebhook] = useState(false)
  const [latestColumnWidths, setLatestColumnWidths] = useState<WidthMap>({
    device: 230,
    datacenter: 130,
    status: 90,
    sync: 130,
    finishedAt: 160,
    action: 100,
  })
  const [searchColumnWidths, setSearchColumnWidths] = useState<WidthMap>({
    device: 220,
    datacenter: 120,
    lineNumber: 70,
    line: 360,
    action: 82,
  })
  const runningConfigScrollRef = useRef<HTMLDivElement | null>(null)
  const startupConfigScrollRef = useRef<HTMLDivElement | null>(null)
  const syncingConfigScrollRef = useRef(false)

  const resizableTitle = (
    title: string,
    key: string,
    widths: WidthMap,
    setWidths: Dispatch<SetStateAction<WidthMap>>,
    minWidth = 70
  ) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
      <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>{title}</span>
      <span
        title="拖动调整列宽"
        onMouseDown={(event) => {
          event.preventDefault()
          event.stopPropagation()
          const startX = event.clientX
          const startWidth = widths[key] || minWidth
          const handleMouseMove = (moveEvent: MouseEvent) => {
            const nextWidth = Math.max(minWidth, startWidth + moveEvent.clientX - startX)
            setWidths((previous) => ({ ...previous, [key]: nextWidth }))
          }
          const handleMouseUp = () => {
            window.removeEventListener('mousemove', handleMouseMove)
            window.removeEventListener('mouseup', handleMouseUp)
          }
          window.addEventListener('mousemove', handleMouseMove)
          window.addEventListener('mouseup', handleMouseUp)
        }}
        style={{
          width: 8,
          height: 18,
          cursor: 'col-resize',
          borderRight: '2px solid rgba(148,163,184,0.55)',
          opacity: 0.75,
          flexShrink: 0,
        }}
      />
    </div>
  )

  const loadLatestResults = async (jobId?: number | null, clearOnEmpty = true) => {
    if (!jobId) {
      if (clearOnEmpty) setLatestResults([])
      setLatestResultsJobId(null)
      return
    }
    if (latestResultsJobId === jobId && latestResults.length > 0) {
      return
    }
    setLatestResultsLoading(true)
    try {
      const detail = await getConfigBackupJob(jobId)
      setLatestResults(detail.results || [])
      setLatestResultsJobId(jobId)
    } catch {
      if (clearOnEmpty) setLatestResults([])
      setLatestResultsJobId(null)
    } finally {
      setLatestResultsLoading(false)
    }
  }

  const loadPageMeta = async () => {
    try {
      const [filters, settings] = await Promise.all([
        getConfigBackupFilters(),
        getConfigBackupSettings(),
      ])
      setDatacenters(filters.datacenters || [])
      const channel = settings.settings?.notification_channels?.[0]
      const webhook = channel?.webhook || channel?.url || ''
      setNotificationWebhook(webhook)
      setSavedNotificationWebhook(webhook)
      setWebhookLabel(detectWebhookLabel(webhook))
      setWebhookEditing(!webhook)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '获取配置备份页面配置失败')
    }
  }

  const refreshJobProgress = async (page = jobsPage, pageSize = jobsPageSize) => {
    const [latest, result] = await Promise.all([
      getLatestConfigBackupJob(),
      getConfigBackupJobs({ skip: (page - 1) * pageSize, limit: pageSize }),
    ])
    setLatestJob(latest.job)
    setJobs(result.items)
    setJobsTotal(result.total)
    if (latest.job?.id && latest.job.id !== latestResultsJobId && !['pending', 'running'].includes(latest.job.status)) {
      window.setTimeout(() => {
        void loadLatestResults(latest.job?.id, false)
      }, 400)
    }
  }

  const loadJobs = async (page = jobsPage, pageSize = jobsPageSize, silent = false) => {
    if (!silent) setJobsLoading(true)
    try {
      const [latest, result] = await Promise.all([
        getLatestConfigBackupJob(),
        getConfigBackupJobs({ skip: (page - 1) * pageSize, limit: pageSize }),
      ])
      setLatestJob(latest.job)
      setJobs(result.items)
      setJobsTotal(result.total)
    } catch (error: any) {
      if (!silent) message.error(error?.response?.data?.detail || '获取配置备份信息失败')
    } finally {
      if (!silent) setJobsLoading(false)
    }
  }

  useEffect(() => {
    loadPageMeta()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    loadJobs(jobsPage, jobsPageSize)
    const timer = window.setInterval(() => {
      refreshJobProgress(jobsPage, jobsPageSize).catch(() => undefined)
    }, latestJob && ['pending', 'running'].includes(latestJob.status) ? 2000 : 5000)
    return () => window.clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobsPage, jobsPageSize, latestJob?.status])

  useEffect(() => {
    if (!latestJob?.id || ['pending', 'running'].includes(latestJob.status)) {
      return undefined
    }
    const timer = window.setTimeout(() => {
      void loadLatestResults(latestJob.id, false)
    }, 700)
    return () => window.clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [latestJob?.id, latestJob?.status])

  const handleTrigger = async () => {
    setTriggering(true)
    const hideLoading = message.loading('正在提交配置备份任务，请稍候...', 0)
    try {
      const result = await triggerConfigBackup()
      hideLoading()
      message.success(result.message || '已提交配置备份任务')
      if (result.job) {
        setLatestJob(result.job)
        setJobs((prev) => [result.job, ...prev.filter((item) => item.id !== result.job.id)].slice(0, jobsPageSize))
      }
      setJobsPage(1)
      window.setTimeout(() => {
        void refreshJobProgress(1, jobsPageSize)
      }, 600)
    } catch (error: any) {
      hideLoading()
      message.error(error?.response?.data?.detail || '触发配置备份失败')
    } finally {
      setTriggering(false)
    }
  }

  const handleCancelJob = async (jobId?: number) => {
    if (!jobId) return
    try {
      const result = await cancelConfigBackupJob(jobId)
      message.success(result.message || '已发送取消指令')
      await refreshJobProgress(jobsPage, jobsPageSize)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '取消任务失败')
    }
  }

  const handleSearch = async (silent = false) => {
    const text = keyword.trim()
    if (!text) {
      setSearchItems([])
      setSearchJob(null)
      if (!silent) {
        message.warning('请输入要搜索的配置内容，例如 IP 地址')
      }
      return
    }
    setSearchLoading(true)
    try {
      const result = await searchConfigBackups({
        keyword: text,
        datacenter,
        device_ip: deviceIp.trim() || undefined,
        limit: 200,
        context_lines: 1,
      })
      setSearchItems(result.items)
      setSearchJob(result.job)
      if (!result.items.length && !silent) {
        message.info('最近一次成功备份中没有搜索到匹配内容')
      }
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '搜索配置失败')
    } finally {
      setSearchLoading(false)
    }
  }

  const openResult = async (resultId: number, lineNumber?: number) => {
    setResultDetail(null)
    setHighlightLine(lineNumber || null)
    setHighlightStartupLine(null)
    setResultDrawerOpen(true)
    setResultLoading(true)
    try {
      const detail = await getConfigBackupResult(resultId)
      setResultDetail(detail)
      if (lineNumber) {
        window.setTimeout(() => {
          document.getElementById(`config-line-${lineNumber}`)?.scrollIntoView({ block: 'center' })
        }, 120)
      } else if (detail.config_sync_diff) {
        const parsed = parseDiffLineSets(detail.config_sync_diff)
        const runningLine = findFirstChangedLine(detail.config_content || '', parsed.added)
        const startupLine = findFirstChangedLine(detail.startup_config_content || '', parsed.removed)
        if (runningLine) {
          setHighlightLine(runningLine)
          window.setTimeout(() => {
            document.getElementById(`config-line-${runningLine}`)?.scrollIntoView({ block: 'center' })
          }, 180)
        } else if (startupLine) {
          setHighlightStartupLine(startupLine)
          window.setTimeout(() => {
            document.getElementById(`startup-config-line-${startupLine}`)?.scrollIntoView({ block: 'center' })
          }, 180)
        }
      }
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '获取配置详情失败')
      setResultDrawerOpen(false)
    } finally {
      setResultLoading(false)
    }
  }

  const openJobResults = async (jobId: number) => {
    setJobResults([])
    setJobResultsOpen(true)
    setJobResultsLoading(true)
    try {
      const detail = await getConfigBackupJob(jobId)
      setJobResults(detail.results || [])
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '获取任务明细失败')
      setJobResultsOpen(false)
    } finally {
      setJobResultsLoading(false)
    }
  }

  const handleSaveWebhook = async () => {
    setSavingWebhook(true)
    try {
      const result = await saveConfigBackupSettings({
        notification_channels: notificationWebhook.trim()
          ? [{ webhook: notificationWebhook.trim() }]
          : [],
      })
      const channel = result.settings.notification_channels?.[0]
      const webhook = channel?.webhook || channel?.url || ''
      setNotificationWebhook(webhook)
      setSavedNotificationWebhook(webhook)
      setWebhookLabel(detectWebhookLabel(webhook))
      setWebhookEditing(!webhook)
      message.success(result.message || '机器人通知已保存')
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '保存机器人通知失败')
    } finally {
      setSavingWebhook(false)
    }
  }

  const handleTestWebhook = async () => {
    const url = (notificationWebhook.trim() || savedNotificationWebhook.trim())
    if (!url) {
      message.warning('请先填写机器人 Webhook 地址')
      return
    }
    setTestingWebhook(true)
    try {
      const result = await testConfigBackupNotification(url)
      message.success(result.message || '测试消息发送成功')
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '测试消息发送失败')
    } finally {
      setTestingWebhook(false)
    }
  }

  const progressPercent = useMemo(() => {
    if (!latestJob?.total_devices) return 0
    return Math.round(((latestJob.success_count + latestJob.failed_count) / latestJob.total_devices) * 100)
  }, [latestJob])
  const hasRunningJob = Boolean(latestJob && ['pending', 'running'].includes(latestJob.status))
  const isCancelledJob = latestJob?.status === 'cancelled'
  const isFinishedJob = Boolean(latestJob && ['success', 'partial_failed', 'failed', 'cancelled'].includes(latestJob.status))
  const completedCount = (latestJob?.success_count || 0) + (latestJob?.failed_count || 0)
  const unfinishedCount = Math.max((latestJob?.total_devices || 0) - completedCount, 0)
  const backupStageText = useMemo(() => {
    if (!latestJob) return ''
    if (latestJob.status === 'pending') return '任务已提交，正在等待配置备份 Worker 接收。'
    if (latestJob.status === 'running') {
      if (!latestJob.total_devices) return '任务已启动，正在加载需要备份的上线设备清单。'
      if (completedCount === 0) return `已启动 ${latestJob.total_devices} 台设备采集，正在并发建立 SSH 连接并读取运行/启动配置，等待首批设备返回。`
      return `正在执行，剩余 ${unfinishedCount} 台设备；部分设备仍在 SSH 登录、分页读取或一致性检查中。`
    }
    return ''
  }, [completedCount, latestJob, unfinishedCount])

  const filteredLatestResults = useMemo(() => {
    const text = resultFilterText.trim().toLowerCase()
    return latestResults.filter((item) =>
      (!text || [
          item.device_name,
          item.device_ip,
          item.datacenter_name,
          item.vendor,
          item.model,
          item.device_type,
        ].some((value) => String(value || '').toLowerCase().includes(text))) &&
      (!resultDatacenterFilter || item.datacenter_name === resultDatacenterFilter) &&
      (!resultVendorFilter || item.vendor === resultVendorFilter) &&
      (!resultDeviceTypeFilter || item.device_type === resultDeviceTypeFilter)
    )
  }, [latestResults, resultDatacenterFilter, resultDeviceTypeFilter, resultFilterText, resultVendorFilter])

  const latestResultOptions = useMemo(() => ({
    datacenters: Array.from(new Set(latestResults.map((item) => item.datacenter_name).filter(Boolean) as string[])).sort(),
    vendors: Array.from(new Set(latestResults.map((item) => item.vendor).filter(Boolean) as string[])).sort(),
    deviceTypes: Array.from(new Set(latestResults.map((item) => item.device_type).filter(Boolean) as string[])).sort(),
  }), [latestResults])

  const jobResultOptions = useMemo(() => ({
    datacenters: uniqueFilterOptions(jobResults.map((item) => item.datacenter_name)),
    statuses: uniqueFilterOptions(jobResults.map((item) => item.status)),
    vendors: uniqueFilterOptions(jobResults.map((item) => item.vendor)),
  }), [jobResults])

  const syncConfigScroll = (source: HTMLDivElement, target: HTMLDivElement | null) => {
    if (!target || syncingConfigScrollRef.current) return
    syncingConfigScrollRef.current = true
    target.scrollTop = source.scrollTop
    target.scrollLeft = source.scrollLeft
    window.requestAnimationFrame(() => {
      syncingConfigScrollRef.current = false
    })
  }

  const diffLineSets = useMemo(() => parseDiffLineSets(resultDetail?.config_sync_diff), [resultDetail?.config_sync_diff])

  const renderConfigLines = (content: string, side: 'running' | 'startup' = 'running') => {
    const changedSet = side === 'running' ? diffLineSets.added : diffLineSets.removed
    return content.split('\n').map((line, index) => {
      const lineNumber = index + 1
      const active = side === 'running' ? highlightLine === lineNumber : highlightStartupLine === lineNumber
      const changed = changedSet.has(line.trim())
      return (
        <div
          key={`${side}-${lineNumber}`}
          id={side === 'running' ? `config-line-${lineNumber}` : `startup-config-line-${lineNumber}`}
          style={{
            display: 'grid',
            gridTemplateColumns: '56px 1fr',
            gap: 10,
            padding: '1px 8px',
            borderRadius: 8,
            background: active ? 'rgba(250, 204, 21, 0.34)' : changed ? (side === 'running' ? 'rgba(251, 146, 60, 0.18)' : 'rgba(248, 113, 113, 0.16)') : 'transparent',
            color: active ? '#7c2d12' : undefined,
            fontWeight: active || changed ? 800 : 500,
          }}
        >
          <Text type="secondary" style={{ userSelect: 'none', textAlign: 'right' }}>{lineNumber}</Text>
          <span style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{side === 'running' ? highlightText(line, keyword.trim()) : line}</span>
        </div>
      )
    })
  }

  const renderConfigContent = () => {
    if (resultLoading) return '正在加载配置内容...'
    const content = resultDetail?.config_content || ''
    if (!content) return '暂无配置内容'
    const startupContent = resultDetail?.startup_config_content || ''
    const showCompare = Boolean(startupContent && resultDetail?.config_sync_status && resultDetail.config_sync_status !== 'matched')
    const header = resultDetail?.config_sync_status ? (
      <div style={{ marginBottom: 12, padding: 12, borderRadius: 10, background: colorBgContainer, border: '1px solid rgba(148,163,184,0.22)', fontFamily: 'inherit' }}>
        <Space wrap>
          <Text strong>运行/启动配置：</Text>
          {renderSyncStatus(resultDetail)}
          {resultDetail.startup_command ? <Text type="secondary">启动配置命令：{resultDetail.startup_command}</Text> : null}
          {resultDetail.config_save_command ? <Text type="secondary">保存命令：{resultDetail.config_save_command}</Text> : null}
        </Space>
        {!showCompare && resultDetail.config_sync_diff ? (
          <pre style={{ margin: '10px 0 0', whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: 180, overflow: 'auto' }}>{resultDetail.config_sync_diff}</pre>
        ) : null}
      </div>
    ) : null

    if (showCompare) {
      const paneStyle = {
        background: colorFillQuaternary,
        borderRadius: 12,
        padding: 12,
        minHeight: '62vh',
        maxHeight: '72vh',
        overflow: 'auto',
        border: '1px solid rgba(148,163,184,0.18)',
      } as const
      return (
        <div style={{ fontFamily: 'Menlo, Consolas, monospace', fontSize: 13, lineHeight: 1.7 }}>
          {header}
          <Row gutter={12}>
            <Col span={12}>
              <div style={{ marginBottom: 8 }}><Tag color="orange">运行配置 current</Tag><Text type="secondary">{resultDetail?.command || '-'}</Text></div>
              <div ref={runningConfigScrollRef} style={paneStyle} onScroll={(event) => syncConfigScroll(event.currentTarget, startupConfigScrollRef.current)}>
                {renderConfigLines(content, 'running')}
              </div>
            </Col>
            <Col span={12}>
              <div style={{ marginBottom: 8 }}><Tag color="red">保存配置 startup</Tag><Text type="secondary">{resultDetail?.startup_command || '-'}</Text></div>
              <div ref={startupConfigScrollRef} style={paneStyle} onScroll={(event) => syncConfigScroll(event.currentTarget, runningConfigScrollRef.current)}>
                {renderConfigLines(startupContent, 'startup')}
              </div>
            </Col>
          </Row>
        </div>
      )
    }

    return (
      <div style={{ fontFamily: 'Menlo, Consolas, monospace', fontSize: 13, lineHeight: 1.7 }}>
        {header}
        {renderConfigLines(content, 'running')}
      </div>
    )
  }

  useEffect(() => {
    const text = keyword.trim()
    if (!text) {
      setSearchItems([])
      setSearchJob(null)
      return
    }
    const timer = window.setTimeout(() => {
      handleSearch(true)
    }, 500)
    return () => window.clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [keyword, datacenter, deviceIp])

  return (
    <div className="modern-page">
      <Row gutter={[16, 16]}>
        <Col span={6}>
          <Card>
            <Statistic title="最近任务状态" value={latestJob ? statusLabel[latestJob.status] || latestJob.status : '暂无'} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="备份设备数" value={latestJob?.total_devices || 0} />
          </Card>
        </Col>
        <Col span={6}>
          <Card style={{ borderColor: latestJob && ['pending', 'running'].includes(latestJob.status) ? '#1677ff' : undefined, boxShadow: latestJob && ['pending', 'running'].includes(latestJob.status) ? '0 0 0 3px rgba(22,119,255,0.10)' : undefined }}>
            <Statistic
              title={latestJob && ['pending', 'running'].includes(latestJob.status) ? '成功 / 总数（实时）' : '成功 / 总数'}
              value={`${latestJob?.success_count || 0} / ${latestJob?.total_devices || 0}`}
              valueStyle={{ color: latestJob?.failed_count ? '#f5222d' : '#16a34a', fontWeight: 800 }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            {hasRunningJob ? (
              <Statistic title="总进度" value={progressPercent} suffix="%" />
            ) : (
              <Statistic
                title="任务结果"
                value={latestJob ? (statusLabel[latestJob.status] || latestJob.status) : '暂无'}
                valueStyle={{
                  color: latestJob?.status === 'success' ? '#16a34a' : latestJob?.status === 'partial_failed' ? '#d97706' : isCancelledJob ? '#64748b' : latestJob?.status === 'failed' ? '#f5222d' : undefined,
                  fontWeight: 800,
                }}
              />
            )}
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={8}>
          <Card
            title="配置备份"
            extra={<Button icon={<ReloadOutlined />} onClick={() => loadJobs(jobsPage, jobsPageSize)} loading={jobsLoading}>刷新</Button>}
            style={{ height: '100%' }}
          >
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              <Text type="secondary">每天 00:00 自动备份所有上线设备；手动触发后异步执行，完成后推送机器人。</Text>
              {hasRunningJob ? (
                <Button block danger onClick={() => handleCancelJob(latestJob?.id)}>
                  停止当前备份任务
                </Button>
              ) : (
                <Button block type="primary" icon={<CloudDownloadOutlined />} onClick={handleTrigger} loading={triggering}>
                  手动触发备份
                </Button>
              )}
              {latestJob && hasRunningJob ? (
                <div>
                  <Progress
                    percent={progressPercent}
                    status="active"
                  />
                  <Space direction="vertical" size={2} style={{ width: '100%' }}>
                    <Text type="secondary">
                      已完成 {completedCount} / {latestJob.total_devices || 0}，成功 {latestJob.success_count || 0}，失败 {latestJob.failed_count || 0}
                    </Text>
                    {backupStageText ? <Text type="secondary">{backupStageText}</Text> : null}
                  </Space>
                </div>
              ) : latestJob && isFinishedJob ? (
                <div style={{ padding: 12, borderRadius: 12, background: colorFillQuaternary }}>
                  <Space direction="vertical" size={4}>
                    <Text strong>{statusLabel[latestJob.status] || latestJob.status}</Text>
                    <Text type="secondary">
                      成功 {latestJob.success_count || 0}，失败 {latestJob.failed_count || 0}
                      {isCancelledJob ? `，未执行 ${unfinishedCount}` : ''}
                    </Text>
                    {isCancelledJob ? (
                      <Text type="secondary">任务已停止，未继续备份，不再显示进行中进度。</Text>
                    ) : null}
                    {latestJob.finished_at ? (
                      <Text type="secondary">结束时间：{formatTime(latestJob.finished_at)}</Text>
                    ) : null}
                  </Space>
                </div>
              ) : latestJob ? (
                <div>
                  <Text type="secondary">
                    成功 {latestJob.success_count || 0}，失败 {latestJob.failed_count || 0}
                  </Text>
                </div>
              ) : null}
              {latestJob?.summary ? (
                <Paragraph
                  ellipsis={{ rows: 5, expandable: true, symbol: '展开' }}
                  style={{ whiteSpace: 'pre-wrap', padding: 12, borderRadius: 12, background: colorFillQuaternary, marginBottom: 0 }}
                >
                  {latestJob.summary}
                </Paragraph>
              ) : null}
            </Space>
          </Card>
        </Col>
        <Col xs={24} xl={8}>
          <Card title="备份任务历史" style={{ height: '100%' }}>
            <Table
              size="small"
              rowKey="id"
              loading={jobsLoading}
              dataSource={jobs}
              pagination={{
                current: jobsPage,
                pageSize: jobsPageSize,
                total: jobsTotal,
                showSizeChanger: true,
                simple: true,
                pageSizeOptions: [5, 10, 20, 50],
                onChange: (page, pageSize) => {
                  setJobsPage(page)
                  setJobsPageSize(pageSize)
                },
              }}
              columns={[
                { title: 'ID', dataIndex: 'id', width: 64 },
                { title: '状态', dataIndex: 'status', width: 92, render: (value: string) => <Tag color={statusColor[value] || 'default'}>{statusLabel[value] || value}</Tag> },
                { title: '成功/失败', width: 92, render: (_: unknown, record: ConfigBackupJob) => `${record.success_count}/${record.failed_count}` },
                { title: '完成时间', dataIndex: 'finished_at', render: formatTime },
                {
                  title: '操作',
                  width: 112,
                  render: (_: unknown, record: ConfigBackupJob) => (
                    <Space size={4}>
                      <Button size="small" onClick={() => openJobResults(record.id)}>明细</Button>
                      {['pending', 'running'].includes(record.status) ? <Button size="small" danger onClick={() => handleCancelJob(record.id)}>停止</Button> : null}
                    </Space>
                  ),
                },
              ]}
            />
          </Card>
        </Col>
        <Col xs={24} xl={8}>
          <Card title="机器人通知配置" style={{ height: '100%' }}>
            {notificationWebhook && !webhookEditing ? (
              <Space direction="vertical" size={12} style={{ width: '100%' }}>
                <Text type="secondary">已保存 Webhook，页面默认脱敏显示。</Text>
                <div style={{ padding: 12, borderRadius: 12, background: colorFillQuaternary, wordBreak: 'break-all' }}>
                  <Text strong>{maskWebhook(notificationWebhook)}</Text>
                  <br />
                  <Text type="secondary">当前识别：{webhookLabel}</Text>
                </div>
                <Space>
                  <Button onClick={handleTestWebhook} loading={testingWebhook}>测试</Button>
                  <Button onClick={() => {
                    setNotificationWebhook('')
                    setWebhookEditing(true)
                  }}>修改</Button>
                </Space>
              </Space>
            ) : (
              <Form layout="vertical">
                <Form.Item
                  label="配置备份完成后推送的机器人 Webhook"
                  extra={`支持飞书 / 企业微信 / 钉钉 / 通用 Webhook，当前识别：${webhookLabel}`}
                >
                  <Input.TextArea
                    allowClear
                    rows={3}
                    value={notificationWebhook}
                    onChange={(event) => {
                      setNotificationWebhook(event.target.value)
                      setWebhookLabel(detectWebhookLabel(event.target.value))
                    }}
                    placeholder="粘贴机器人 Webhook 地址"
                  />
                </Form.Item>
                <Space>
                  <Button onClick={handleTestWebhook} loading={testingWebhook}>测试</Button>
                  <Button type="primary" onClick={handleSaveWebhook} loading={savingWebhook}>保存</Button>
                  {savedNotificationWebhook ? <Button onClick={() => {
                    setNotificationWebhook(savedNotificationWebhook)
                    setWebhookLabel(detectWebhookLabel(savedNotificationWebhook))
                    setWebhookEditing(false)
                  }}>取消</Button> : null}
                </Space>
              </Form>
            )}
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]}>
        <Col xs={24} xl={12}>
          <Card
            title="最近备份配置文件"
            style={{ height: 680 }}
            extra={<Text type="secondary">共 {filteredLatestResults.length} 台</Text>}
          >
            <Space wrap style={{ marginBottom: 12 }}>
              <Select allowClear showSearch placeholder="机房" value={resultDatacenterFilter} onChange={setResultDatacenterFilter} style={{ width: 160 }} options={latestResultOptions.datacenters.map((value) => ({ value, label: value }))} />
              <Select allowClear showSearch placeholder="厂商" value={resultVendorFilter} onChange={setResultVendorFilter} style={{ width: 130 }} options={latestResultOptions.vendors.map((value) => ({ value, label: value }))} />
              <Select allowClear showSearch placeholder="设备类型" value={resultDeviceTypeFilter} onChange={setResultDeviceTypeFilter} style={{ width: 130 }} options={latestResultOptions.deviceTypes.map((value) => ({ value, label: value }))} />
              <Input allowClear placeholder="搜索设备 / IP" value={resultFilterText} onChange={(event) => setResultFilterText(event.target.value)} style={{ width: 180 }} />
            </Space>
            <Table
              size="small"
              rowKey="id"
              loading={latestResultsLoading}
              dataSource={filteredLatestResults}
              pagination={false}
              virtual
              scroll={{ x: 860, y: 470 }}
              columns={[
                { title: resizableTitle('设备', 'device', latestColumnWidths, setLatestColumnWidths, 160), width: latestColumnWidths.device, sorter: (a, b) => ipToNumber(a.device_ip) - ipToNumber(b.device_ip), render: (_: unknown, record: ConfigBackupResult) => <Space direction="vertical" size={0}><Text strong>{record.device_name}</Text><Text type="secondary">{record.device_ip}</Text></Space> },
                { title: resizableTitle('机房', 'datacenter', latestColumnWidths, setLatestColumnWidths, 90), dataIndex: 'datacenter_name', width: latestColumnWidths.datacenter, filters: latestResultOptions.datacenters.map((value) => ({ text: value, value })), onFilter: (value, record) => record.datacenter_name === value, sorter: (a, b) => String(a.datacenter_name || '').localeCompare(String(b.datacenter_name || '')), render: (value?: string) => value || '-' },
                { title: resizableTitle('状态', 'status', latestColumnWidths, setLatestColumnWidths, 80), dataIndex: 'status', width: latestColumnWidths.status, filters: uniqueFilterOptions(latestResults.map((item) => item.status)), onFilter: (value, record) => record.status === value, sorter: (a, b) => String(a.status).localeCompare(String(b.status)), render: (value: string) => <Tag color={value === 'success' ? 'green' : value === 'failed' ? 'red' : value === 'pending' ? 'blue' : 'default'}>{value === 'success' ? '成功' : value === 'failed' ? '失败' : value === 'pending' ? '等待' : value}</Tag> },
                { title: resizableTitle('配置一致性', 'sync', latestColumnWidths, setLatestColumnWidths, 110), dataIndex: 'config_sync_status', width: latestColumnWidths.sync, filters: uniqueFilterOptions(latestResults.map((item) => syncStatusMeta[item.config_sync_status || '']?.label || item.config_sync_status)), onFilter: (value, record) => (syncStatusMeta[record.config_sync_status || '']?.label || record.config_sync_status) === value, render: (_: unknown, record: ConfigBackupResult) => renderSyncStatus(record) },
                { title: resizableTitle('完成时间', 'finishedAt', latestColumnWidths, setLatestColumnWidths, 130), dataIndex: 'finished_at', width: latestColumnWidths.finishedAt, sorter: (a, b) => new Date(a.finished_at || 0).getTime() - new Date(b.finished_at || 0).getTime(), render: formatTime },
                { title: resizableTitle('操作', 'action', latestColumnWidths, setLatestColumnWidths, 80), width: latestColumnWidths.action, render: (_: unknown, record: ConfigBackupResult) => <Button size="small" icon={<EyeOutlined />} disabled={record.status !== 'success'} onClick={() => openResult(record.id)}>查看</Button> },
              ]}
            />
          </Card>
        </Col>
        <Col xs={24} xl={12}>
          <Card title="配置内容搜索" style={{ height: 680 }}>
            <Space wrap style={{ marginBottom: 12 }}>
              <Input allowClear prefix={<SearchOutlined />} placeholder="搜索 IP / 关键字，例如 10.239.0.1" value={keyword} onChange={(event) => setKeyword(event.target.value)} onPressEnter={() => handleSearch(false)} style={{ width: 260 }} />
              <Select allowClear showSearch placeholder="按机房过滤" value={datacenter} onChange={setDatacenter} style={{ width: 160 }} optionFilterProp="label" options={datacenters.map((item) => ({ value: item.name, label: item.name }))} />
              <Input allowClear placeholder="按设备管理地址过滤" value={deviceIp} onChange={(event) => setDeviceIp(event.target.value)} onPressEnter={() => handleSearch(false)} style={{ width: 170 }} />
              <Button icon={<SearchOutlined />} onClick={() => handleSearch(false)} loading={searchLoading}>刷新</Button>
              {searchJob ? <Text type="secondary">任务 #{searchJob.id}</Text> : null}
            </Space>
            <Table
              size="small"
              rowKey={(record) => `${record.result_id}-${record.line_number}`}
              loading={searchLoading}
              dataSource={searchItems}
              pagination={false}
              scroll={{ x: 860, y: 470 }}
              columns={[
                {
                  title: resizableTitle('设备', 'device', searchColumnWidths, setSearchColumnWidths, 160),
                  width: searchColumnWidths.device,
                  render: (_: unknown, record: ConfigSearchMatch) => (
                    <Space direction="vertical" size={0}>
                      <Text strong>{record.device_name}</Text>
                      <Text type="secondary">{record.device_ip}</Text>
                    </Space>
                  ),
                },
                { title: resizableTitle('机房', 'datacenter', searchColumnWidths, setSearchColumnWidths, 90), dataIndex: 'datacenter_name', width: searchColumnWidths.datacenter, filters: uniqueFilterOptions(searchItems.map((item) => item.datacenter_name)), onFilter: (value, record) => record.datacenter_name === value, sorter: (a, b) => String(a.datacenter_name || '').localeCompare(String(b.datacenter_name || '')), render: (value?: string) => value || '-' },
                { title: resizableTitle('行号', 'lineNumber', searchColumnWidths, setSearchColumnWidths, 62), dataIndex: 'line_number', width: searchColumnWidths.lineNumber, sorter: (a, b) => a.line_number - b.line_number },
                { title: resizableTitle('匹配内容', 'line', searchColumnWidths, setSearchColumnWidths, 220), dataIndex: 'line', width: searchColumnWidths.line, sorter: (a, b) => String(a.line || '').localeCompare(String(b.line || '')), render: (value: string) => <code style={{ whiteSpace: 'pre-wrap' }}>{highlightText(value, keyword.trim())}</code> },
                { title: resizableTitle('操作', 'action', searchColumnWidths, setSearchColumnWidths, 70), width: searchColumnWidths.action, render: (_: unknown, record: ConfigSearchMatch) => <Button size="small" icon={<EyeOutlined />} onClick={() => openResult(record.result_id, record.line_number)}>查看</Button> },
              ]}
              expandable={{
                expandedRowRender: (record) => (
                  <div style={{ background: colorBgContainer, borderRadius: 10, padding: 12 }}>
                    {record.context.map((item) => (
                      <div key={item.line_number} style={{ display: 'grid', gridTemplateColumns: '70px 1fr', gap: 12, fontFamily: 'monospace' }}>
                        <Text type="secondary">{item.line_number}</Text>
                        <span>{highlightText(item.text, keyword.trim())}</span>
                      </div>
                    ))}
                  </div>
                ),
              }}
            />
          </Card>
        </Col>
      </Row>

      <Drawer
        title={resultDetail ? `${resultDetail.device_name} (${resultDetail.device_ip}) 配置` : '配置详情'}
        open={resultDrawerOpen}
        onClose={() => setResultDrawerOpen(false)}
        width="min(1380px, 92vw)"
      >
        <Spin spinning={resultLoading}>
          <div style={{ background: colorFillQuaternary, borderRadius: 12, padding: 16, minHeight: '60vh', maxHeight: '74vh', overflow: 'auto' }}>
            {renderConfigContent()}
          </div>
        </Spin>
      </Drawer>

      <Drawer
        title="任务设备明细"
        open={jobResultsOpen}
        onClose={() => setJobResultsOpen(false)}
        width="96vw"
        bodyStyle={{ padding: 16, height: 'calc(100vh - 55px)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}
      >
        <Table
          rowKey="id"
          loading={jobResultsLoading}
          dataSource={jobResults}
          pagination={{ defaultPageSize: 20, showSizeChanger: true, pageSizeOptions: [10, 20, 50, 100], showTotal: (total) => `共 ${total} 台` }}
          virtual
          scroll={{ x: 1540, y: Math.max(520, window.innerHeight - 205) }}
          style={{ flex: 1, minHeight: 0 }}
          columns={[
            { title: '设备', width: 340, sorter: (a, b) => ipToNumber(a.device_ip) - ipToNumber(b.device_ip), render: (_: unknown, record: ConfigBackupResult) => <Space direction="vertical" size={0}><Text>{record.device_name}</Text><Text type="secondary">{record.device_ip}</Text></Space> },
            { title: '机房', dataIndex: 'datacenter_name', width: 150, filters: jobResultOptions.datacenters, onFilter: (value, record) => record.datacenter_name === value, sorter: (a, b) => String(a.datacenter_name || '').localeCompare(String(b.datacenter_name || '')), render: (value?: string) => value || '-' },
            { title: '命令', dataIndex: 'command', width: 170, filters: uniqueFilterOptions(jobResults.map((item) => item.command)), onFilter: (value, record) => record.command === value, render: (value?: string) => value || '-' },
            { title: '行数', dataIndex: 'line_count', width: 90, sorter: (a, b) => (a.line_count || 0) - (b.line_count || 0), render: (value?: number) => value || '-' },
            { title: '状态', dataIndex: 'status', width: 100, filters: jobResultOptions.statuses, onFilter: (value, record) => record.status === value, sorter: (a, b) => String(a.status).localeCompare(String(b.status)), render: (value: string) => <Tag color={value === 'success' ? 'green' : value === 'failed' ? 'red' : value === 'pending' ? 'blue' : 'default'}>{value === 'success' ? '成功' : value === 'failed' ? '失败' : value === 'pending' ? '等待' : value}</Tag> },
            { title: '配置一致性', dataIndex: 'config_sync_status', width: 150, filters: uniqueFilterOptions(jobResults.map((item) => syncStatusMeta[item.config_sync_status || '']?.label || item.config_sync_status)), onFilter: (value, record) => (syncStatusMeta[record.config_sync_status || '']?.label || record.config_sync_status) === value, render: (_: unknown, record: ConfigBackupResult) => renderSyncStatus(record) },
            { title: '错误信息', dataIndex: 'error_message', width: 260, ellipsis: true, render: (value?: string) => value ? <Tooltip title={value}>{value}</Tooltip> : <Text type="secondary">-</Text> },
            { title: '完成时间', dataIndex: 'finished_at', width: 180, render: formatTime },
            {
              title: '操作',
              width: 120,
              render: (_: unknown, record: ConfigBackupResult) => (
                <Button size="small" icon={<EyeOutlined />} disabled={record.status !== 'success'} onClick={() => openResult(record.id)}>查看配置</Button>
              ),
            },
          ]}
        />
        <Text type="secondary" style={{ display: 'block', marginTop: 8, flexShrink: 0 }}>失败设备通常是 SSH 账号、密码、ACL、登录超时或厂商分页命令不兼容导致。</Text>
      </Drawer>
    </div>
  )
}

export default ConfigBackups
