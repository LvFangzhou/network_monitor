import { useEffect, useMemo, useState } from 'react'
import { Button, Card, Col, Drawer, Form, Input, Row, Select, Space, Statistic, Table, Tag, Tooltip, Typography, message, theme } from 'antd'
import { CloudDownloadOutlined, EyeOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import {
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
}

const statusLabel: Record<string, string> = {
  pending: '等待中',
  running: '备份中',
  success: '成功',
  partial_failed: '部分失败',
  failed: '失败',
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
  const [resultDetail, setResultDetail] = useState<ConfigBackupResult | null>(null)
  const [jobResultsOpen, setJobResultsOpen] = useState(false)
  const [jobResults, setJobResults] = useState<ConfigBackupResult[]>([])
  const [latestResults, setLatestResults] = useState<ConfigBackupResult[]>([])
  const [resultFilterText, setResultFilterText] = useState('')
  const [resultDatacenterFilter, setResultDatacenterFilter] = useState<string>()
  const [resultVendorFilter, setResultVendorFilter] = useState<string>()
  const [resultDeviceTypeFilter, setResultDeviceTypeFilter] = useState<string>()
  const [notificationWebhook, setNotificationWebhook] = useState('')
  const [webhookEditing, setWebhookEditing] = useState(false)
  const [webhookLabel, setWebhookLabel] = useState('未填写')
  const [savingWebhook, setSavingWebhook] = useState(false)
  const [testingWebhook, setTestingWebhook] = useState(false)

  const refreshJobProgress = async (page = jobsPage, pageSize = jobsPageSize) => {
    const [latest, result] = await Promise.all([
      getLatestConfigBackupJob(),
      getConfigBackupJobs({ skip: (page - 1) * pageSize, limit: pageSize }),
    ])
    setLatestJob(latest.job)
    setJobs(result.items)
    setJobsTotal(result.total)
    if (latest.job?.id) {
      try {
        const detail = await getConfigBackupJob(latest.job.id)
        setLatestResults(detail.results || [])
      } catch {
        // 保持已有数据，避免轮询瞬时失败导致表格闪空。
      }
    }
  }

  const loadJobs = async (page = jobsPage, pageSize = jobsPageSize, silent = false) => {
    if (!silent) setJobsLoading(true)
    try {
      const [latest, result, filters, settings] = await Promise.all([
        getLatestConfigBackupJob(),
        getConfigBackupJobs({ skip: (page - 1) * pageSize, limit: pageSize }),
        getConfigBackupFilters(),
        getConfigBackupSettings(),
      ])
      setLatestJob(latest.job)
      setJobs(result.items)
      setJobsTotal(result.total)
      setDatacenters(filters.datacenters || [])
      const channel = settings.settings?.notification_channels?.[0]
      const webhook = channel?.webhook || channel?.url || ''
      setNotificationWebhook(webhook)
      setWebhookLabel(detectWebhookLabel(webhook))
      setWebhookEditing(!webhook)
      if (latest.job?.id) {
        try {
          const detail = await getConfigBackupJob(latest.job.id)
          setLatestResults(detail.results || [])
        } catch {
          setLatestResults([])
        }
      } else {
        setLatestResults([])
      }
    } catch (error: any) {
      if (!silent) message.error(error?.response?.data?.detail || '获取配置备份信息失败')
    } finally {
      if (!silent) setJobsLoading(false)
    }
  }

  useEffect(() => {
    loadJobs(1)
    const timer = window.setInterval(() => {
      refreshJobProgress(jobsPage, jobsPageSize).catch(() => undefined)
    }, latestJob && ['pending', 'running'].includes(latestJob.status) ? 2000 : 5000)
    return () => window.clearInterval(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobsPage, jobsPageSize, latestJob?.status])

  const handleTrigger = async () => {
    setTriggering(true)
    try {
      const result = await triggerConfigBackup()
      message.success(result.message || '已提交配置备份任务')
      await loadJobs(1, jobsPageSize)
      setJobsPage(1)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '触发配置备份失败')
    } finally {
      setTriggering(false)
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
    const url = notificationWebhook.trim()
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

  const successRate = useMemo(() => {
    if (!latestJob?.total_devices) return 0
    return Math.round((latestJob.success_count / latestJob.total_devices) * 100)
  }, [latestJob])

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
              title={latestJob && ['pending', 'running'].includes(latestJob.status) ? '成功 / 失败（实时）' : '成功 / 失败'}
              value={`${latestJob?.success_count || 0} / ${latestJob?.failed_count || 0}`}
              valueStyle={{ color: latestJob?.failed_count ? '#f5222d' : '#16a34a', fontWeight: 800 }}
            />
          </Card>
        </Col>
        <Col span={6}>
          <Card>
            <Statistic title="成功率" value={successRate} suffix="%" />
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
              <Button block type="primary" icon={<CloudDownloadOutlined />} onClick={handleTrigger} loading={triggering}>
                手动触发备份
              </Button>
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
                  loadJobs(page, pageSize)
                },
              }}
              columns={[
                { title: 'ID', dataIndex: 'id', width: 64 },
                { title: '状态', dataIndex: 'status', width: 92, render: (value: string) => <Tag color={statusColor[value] || 'default'}>{statusLabel[value] || value}</Tag> },
                { title: '成功/失败', width: 92, render: (_: unknown, record: ConfigBackupJob) => `${record.success_count}/${record.failed_count}` },
                { title: '完成时间', dataIndex: 'finished_at', render: formatTime },
                { title: '操作', width: 84, render: (_: unknown, record: ConfigBackupJob) => <Button size="small" onClick={() => openJobResults(record.id)}>明细</Button> },
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
                  <Button onClick={() => setWebhookEditing(true)}>修改</Button>
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
                  {notificationWebhook ? <Button onClick={() => setWebhookEditing(false)}>取消</Button> : null}
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
              dataSource={filteredLatestResults}
              pagination={false}
              scroll={{ y: 470 }}
              columns={[
                { title: '设备', width: 230, render: (_: unknown, record: ConfigBackupResult) => <Space direction="vertical" size={0}><Text strong>{record.device_name}</Text><Text type="secondary">{record.device_ip}</Text></Space> },
                { title: '机房', dataIndex: 'datacenter_name', width: 130, render: (value?: string) => value || '-' },
                { title: '状态', dataIndex: 'status', width: 90, render: (value: string) => <Tag color={value === 'success' ? 'green' : value === 'failed' ? 'red' : 'blue'}>{value === 'success' ? '成功' : value === 'failed' ? '失败' : value}</Tag> },
                { title: '完成时间', dataIndex: 'finished_at', width: 160, render: formatTime },
                { title: '操作', width: 100, render: (_: unknown, record: ConfigBackupResult) => <Button size="small" icon={<EyeOutlined />} disabled={record.status !== 'success'} onClick={() => openResult(record.id)}>查看</Button> },
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
              scroll={{ y: 470 }}
              columns={[
                {
                  title: '设备',
                  width: 220,
                  render: (_: unknown, record: ConfigSearchMatch) => (
                    <Space direction="vertical" size={0}>
                      <Text strong>{record.device_name}</Text>
                      <Text type="secondary">{record.device_ip}</Text>
                    </Space>
                  ),
                },
                { title: '机房', dataIndex: 'datacenter_name', width: 120, render: (value?: string) => value || '-' },
                { title: '行号', dataIndex: 'line_number', width: 70 },
                { title: '匹配内容', dataIndex: 'line', render: (value: string) => <code style={{ whiteSpace: 'pre-wrap' }}>{highlightText(value, keyword.trim())}</code> },
                { title: '操作', width: 82, render: (_: unknown, record: ConfigSearchMatch) => <Button size="small" icon={<EyeOutlined />} onClick={() => openResult(record.result_id)}>查看</Button> },
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
          pagination={{ defaultPageSize: 20, showSizeChanger: true, pageSizeOptions: [10, 20, 50, 100], showTotal: (total) => `共 ${total} 台` }}
          columns={[
            { title: '设备', width: 260, render: (_: unknown, record: ConfigBackupResult) => <Space direction="vertical" size={0}><Text>{record.device_name}</Text><Text type="secondary">{record.device_ip}</Text></Space> },
            { title: '机房', dataIndex: 'datacenter_name', width: 150, render: (value?: string) => value || '-' },
            { title: '命令', dataIndex: 'command', width: 180, render: (value?: string) => value || '-' },
            { title: '行数', dataIndex: 'line_count', width: 90, render: (value?: number) => value || '-' },
            { title: '状态', dataIndex: 'status', width: 100, render: (value: string) => <Tag color={value === 'success' ? 'green' : value === 'failed' ? 'red' : 'blue'}>{value === 'success' ? '成功' : value === 'failed' ? '失败' : value}</Tag> },
            { title: '错误信息', dataIndex: 'error_message', ellipsis: true, render: (value?: string) => value ? <Tooltip title={value}>{value}</Tooltip> : <Text type="secondary">-</Text> },
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
        <Text type="secondary" style={{ display: 'block', marginTop: 12 }}>失败设备通常是 SSH 账号、密码、ACL、登录超时或厂商分页命令不兼容导致。</Text>
      </Drawer>
    </div>
  )
}

export default ConfigBackups
