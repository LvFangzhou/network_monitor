import { useEffect, useMemo, useState } from 'react'
import { Button, Card, Col, Drawer, Input, Row, Select, Space, Statistic, Table, Tag, Tooltip, Typography, message, theme } from 'antd'
import { CloudDownloadOutlined, EyeOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import {
  getConfigBackupFilters,
  getConfigBackupJob,
  getConfigBackupJobs,
  getConfigBackupResult,
  getLatestConfigBackupJob,
  searchConfigBackups,
  triggerConfigBackup,
  type ConfigBackupJob,
  type ConfigBackupResult,
  type ConfigSearchMatch,
} from '../api/configBackups'

const { Text, Paragraph } = Typography

const statusColor: Record<string, string> = {
  pending: 'blue',
  running: 'processing',
  success: 'green',
  partial_failed: 'orange',
  failed: 'red',
}

const statusLabel: Record<string, string> = {
  pending: '等待中',
  running: '备份中',
  success: '成功',
  partial_failed: '部分失败',
  failed: '失败',
}

const formatTime = (value?: string | null) => value ? new Date(value).toLocaleString() : '-'

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

const ConfigBackups = () => {
  const {
    token: { colorBgContainer, colorFillQuaternary },
  } = theme.useToken()
  const [latestJob, setLatestJob] = useState<ConfigBackupJob | null>(null)
  const [jobs, setJobs] = useState<ConfigBackupJob[]>([])
  const [jobsTotal, setJobsTotal] = useState(0)
  const [jobsPage, setJobsPage] = useState(1)
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
  const [resultDetail, setResultDetail] = useState<ConfigBackupResult | null>(null)
  const [jobResultsOpen, setJobResultsOpen] = useState(false)
  const [jobResults, setJobResults] = useState<ConfigBackupResult[]>([])

  const loadJobs = async (page = jobsPage) => {
    setJobsLoading(true)
    try {
      const [latest, result, filters] = await Promise.all([
        getLatestConfigBackupJob(),
        getConfigBackupJobs({ skip: (page - 1) * 10, limit: 10 }),
        getConfigBackupFilters(),
      ])
      setLatestJob(latest.job)
      setJobs(result.items)
      setJobsTotal(result.total)
      setDatacenters(filters.datacenters || [])
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '获取配置备份信息失败')
    } finally {
      setJobsLoading(false)
    }
  }

  useEffect(() => {
    loadJobs(1)
    const timer = window.setInterval(() => {
      getLatestConfigBackupJob().then((result) => setLatestJob(result.job)).catch(() => undefined)
    }, 10000)
    return () => window.clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleTrigger = async () => {
    setTriggering(true)
    try {
      const result = await triggerConfigBackup()
      message.success(result.message || '已提交配置备份任务')
      await loadJobs(1)
      setJobsPage(1)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '触发配置备份失败')
    } finally {
      setTriggering(false)
    }
  }

  const handleSearch = async () => {
    const text = keyword.trim()
    if (!text) {
      message.warning('请输入要搜索的配置内容，例如 IP 地址')
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
      if (!result.items.length) {
        message.info('最近一次成功备份中没有搜索到匹配内容')
      }
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '搜索配置失败')
    } finally {
      setSearchLoading(false)
    }
  }

  const openResult = async (resultId: number) => {
    try {
      const detail = await getConfigBackupResult(resultId)
      setResultDetail(detail)
      setResultDrawerOpen(true)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '获取配置详情失败')
    }
  }

  const openJobResults = async (jobId: number) => {
    try {
      const detail = await getConfigBackupJob(jobId)
      setJobResults(detail.results || [])
      setJobResultsOpen(true)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '获取任务明细失败')
    }
  }

  const successRate = useMemo(() => {
    if (!latestJob?.total_devices) return 0
    return Math.round((latestJob.success_count / latestJob.total_devices) * 100)
  }, [latestJob])

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
          <Card>
            <Statistic title="成功 / 失败" value={`${latestJob?.success_count || 0} / ${latestJob?.failed_count || 0}`} />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="成功率" value={successRate} suffix="%" />
          </Card>
        </Col>
      </Row>

      <Card
        title="配置备份"
        extra={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={() => loadJobs(jobsPage)} loading={jobsLoading}>刷新</Button>
            <Button type="primary" icon={<CloudDownloadOutlined />} onClick={handleTrigger} loading={triggering}>
              手动触发备份
            </Button>
          </Space>
        }
      >
        <Space direction="vertical" size={10} style={{ width: '100%' }}>
          <Text type="secondary">系统每天 00:00 自动对所有上线设备执行配置备份。手动触发会异步执行，完成后通过机器人推送各机房成功/失败数量。</Text>
          {latestJob?.summary ? (
            <Paragraph style={{ whiteSpace: 'pre-wrap', padding: 12, borderRadius: 12, background: colorFillQuaternary, marginBottom: 0 }}>
              {latestJob.summary}
            </Paragraph>
          ) : null}
        </Space>
      </Card>

      <Card title="配置内容搜索">
        <Space wrap style={{ marginBottom: 16 }}>
          <Input
            allowClear
            prefix={<SearchOutlined />}
            placeholder="搜索 IP / 关键字，例如 10.239.0.1"
            value={keyword}
            onChange={(event) => setKeyword(event.target.value)}
            onPressEnter={handleSearch}
            style={{ width: 320 }}
          />
          <Select
            allowClear
            showSearch
            placeholder="按机房过滤"
            value={datacenter}
            onChange={setDatacenter}
            style={{ width: 200 }}
            optionFilterProp="label"
            options={datacenters.map((item) => ({ value: item.name, label: item.name }))}
          />
          <Input
            allowClear
            placeholder="按设备管理地址过滤"
            value={deviceIp}
            onChange={(event) => setDeviceIp(event.target.value)}
            onPressEnter={handleSearch}
            style={{ width: 220 }}
          />
          <Button type="primary" icon={<SearchOutlined />} onClick={handleSearch} loading={searchLoading}>搜索</Button>
          {searchJob ? <Text type="secondary">搜索范围：任务 #{searchJob.id}，完成时间 {formatTime(searchJob.finished_at)}</Text> : null}
        </Space>
        <Table
          rowKey={(record) => `${record.result_id}-${record.line_number}`}
          loading={searchLoading}
          dataSource={searchItems}
          pagination={{ pageSize: 20, showSizeChanger: true }}
          columns={[
            {
              title: '设备',
              width: 280,
              render: (_: unknown, record: ConfigSearchMatch) => (
                <Space direction="vertical" size={0}>
                  <Text strong>{record.device_name}</Text>
                  <Text type="secondary">{record.device_ip}</Text>
                </Space>
              ),
            },
            { title: '机房', dataIndex: 'datacenter_name', width: 150, render: (value?: string) => value || '-' },
            { title: '厂商/型号', width: 180, render: (_: unknown, record: ConfigSearchMatch) => [record.vendor, record.model].filter(Boolean).join(' / ') || '-' },
            { title: '行号', dataIndex: 'line_number', width: 90 },
            {
              title: '匹配内容',
              dataIndex: 'line',
              render: (value: string) => <code style={{ whiteSpace: 'pre-wrap' }}>{highlightText(value, keyword.trim())}</code>,
            },
            {
              title: '操作',
              width: 100,
              render: (_: unknown, record: ConfigSearchMatch) => (
                <Button size="small" icon={<EyeOutlined />} onClick={() => openResult(record.result_id)}>查看配置</Button>
              ),
            },
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

      <Card title="备份任务历史">
        <Table
          rowKey="id"
          loading={jobsLoading}
          dataSource={jobs}
          pagination={{
            current: jobsPage,
            pageSize: 10,
            total: jobsTotal,
            onChange: (page) => {
              setJobsPage(page)
              loadJobs(page)
            },
          }}
          columns={[
            { title: '任务ID', dataIndex: 'id', width: 90 },
            {
              title: '状态',
              dataIndex: 'status',
              width: 120,
              render: (value: string) => <Tag color={statusColor[value] || 'default'}>{statusLabel[value] || value}</Tag>,
            },
            { title: '触发方式', dataIndex: 'trigger_type', width: 110, render: (value: string) => value === 'scheduled' ? '定时' : '手动' },
            { title: '设备数', dataIndex: 'total_devices', width: 100 },
            { title: '成功', dataIndex: 'success_count', width: 100 },
            { title: '失败', dataIndex: 'failed_count', width: 100 },
            { title: '触发人', dataIndex: 'started_by', width: 120, render: (value?: string) => value || '-' },
            { title: '开始时间', dataIndex: 'started_at', width: 180, render: formatTime },
            { title: '完成时间', dataIndex: 'finished_at', width: 180, render: formatTime },
            {
              title: '操作',
              width: 120,
              render: (_: unknown, record: ConfigBackupJob) => (
                <Button size="small" onClick={() => openJobResults(record.id)}>查看明细</Button>
              ),
            },
          ]}
        />
      </Card>

      <Drawer
        title={resultDetail ? `${resultDetail.device_name} (${resultDetail.device_ip}) 配置` : '配置详情'}
        open={resultDrawerOpen}
        onClose={() => setResultDrawerOpen(false)}
        width="80%"
      >
        <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', background: colorFillQuaternary, borderRadius: 12, padding: 16 }}>
          {resultDetail?.config_content || '暂无配置内容'}
        </pre>
      </Drawer>

      <Drawer title="任务设备明细" open={jobResultsOpen} onClose={() => setJobResultsOpen(false)} width="78%">
        <Table
          rowKey="id"
          dataSource={jobResults}
          pagination={{ pageSize: 20 }}
          columns={[
            { title: '设备', width: 260, render: (_: unknown, record: ConfigBackupResult) => <Space direction="vertical" size={0}><Text>{record.device_name}</Text><Text type="secondary">{record.device_ip}</Text></Space> },
            { title: '机房', dataIndex: 'datacenter_name', width: 150, render: (value?: string) => value || '-' },
            { title: '命令', dataIndex: 'command', width: 180, render: (value?: string) => value || '-' },
            { title: '行数', dataIndex: 'line_count', width: 90, render: (value?: number) => value || '-' },
            { title: '状态', dataIndex: 'status', width: 100, render: (value: string) => <Tag color={value === 'success' ? 'green' : value === 'failed' ? 'red' : 'blue'}>{value === 'success' ? '成功' : value === 'failed' ? '失败' : value}</Tag> },
            { title: '错误信息', dataIndex: 'error_message', ellipsis: true, render: (value?: string) => value ? <Tooltip title={value}>{value}</Tooltip> : <Text type="secondary">-</Text> },
            { title: '完成时间', dataIndex: 'finished_at', width: 180, render: formatTime },
          ]}
        />
        <Text type="secondary" style={{ display: 'block', marginTop: 12 }}>失败设备通常是 SSH 账号、密码、ACL、登录超时或厂商分页命令不兼容导致。</Text>
      </Drawer>
    </div>
  )
}

export default ConfigBackups
