import { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import { DeleteOutlined, EditOutlined, EyeOutlined, PlusOutlined, ReloadOutlined } from '@ant-design/icons'
import {
  createUser,
  deleteUser,
  getAuditLogs,
  getMenuOptions,
  getUsers,
  updateCurrentUser,
  updateUser,
  type AuditLog,
  type User,
} from '../api/auth'
import {
  getControllerAssets,
  getControllerOpticals,
  getControllerSettings,
  testController,
  updateControllerSettings,
  type ControllerCheck,
  type ControllerSettings,
} from '../api/controller'
import { useAuthStore } from '../store/auth'

const tablePagination = {
  defaultPageSize: 20,
  showSizeChanger: true,
  pageSizeOptions: [10, 20, 50, 100],
  showTotal: (total: number, range: [number, number]) => `第 ${range[0]}-${range[1]} 条 / 共 ${total} 条`,
}

const ControllerIntegrationPanel = () => {
  const [form] = Form.useForm<ControllerSettings>()
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [checks, setChecks] = useState<ControllerCheck[]>([])
  const [assetsLoading, setAssetsLoading] = useState(false)
  const [opticalsLoading, setOpticalsLoading] = useState(false)
  const [assets, setAssets] = useState<any[]>([])
  const [opticals, setOpticals] = useState<any[]>([])
  const [assetTotal, setAssetTotal] = useState(0)
  const [opticalTotal, setOpticalTotal] = useState(0)

  const loadSettings = async () => {
    setLoading(true)
    try {
      const settings = await getControllerSettings()
      form.setFieldsValue(settings)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '获取控制器配置失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadSettings()
  }, [])

  const handleSave = async () => {
    try {
      const values = await form.validateFields()
      setSaving(true)
      const saved = await updateControllerSettings(values)
      form.setFieldsValue(saved)
      message.success('控制器配置已保存')
    } catch (error: any) {
      if (!error?.errorFields) {
        message.error(error?.response?.data?.detail || '保存控制器配置失败')
      }
    } finally {
      setSaving(false)
    }
  }

  const handleTest = async () => {
    try {
      const values = await form.validateFields()
      setTesting(true)
      const result = await testController(values)
      setChecks(result.checks || [])
      if (result.ok) {
        message.success('控制器连通性测试通过')
      } else {
        message.warning('控制器部分接口测试失败，请查看明细')
      }
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '测试控制器失败')
    } finally {
      setTesting(false)
    }
  }

  const loadAssetSample = async () => {
    setAssetsLoading(true)
    try {
      const result = await getControllerAssets({ page: 1, page_size: 10 })
      setAssets(result.items || [])
      setAssetTotal(result.total || 0)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '读取控制器资产失败')
    } finally {
      setAssetsLoading(false)
    }
  }

  const loadOpticalSample = async () => {
    setOpticalsLoading(true)
    try {
      const result = await getControllerOpticals({ page: 1, page_size: 10, hours: 3 })
      setOpticals(result.items || [])
      setOpticalTotal(result.total || 0)
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '读取光模块数据失败')
    } finally {
      setOpticalsLoading(false)
    }
  }

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Alert
        type="info"
        showIcon
        message="控制器集成先用于验证北向 API 能力"
        description="当前不会替换现有 SNMP/SSH 采集，只提供配置、连通性测试和样例数据读取；确认稳定后再接入光模块信息查询和高精度无损菜单。"
      />
      <Card
        title="控制器 API 配置"
        loading={loading}
        extra={
          <Space>
            <Button icon={<ReloadOutlined />} onClick={loadSettings}>
              刷新
            </Button>
            <Button onClick={handleTest} loading={testing}>
              测试连通性
            </Button>
            <Button type="primary" onClick={handleSave} loading={saving}>
              保存配置
            </Button>
          </Space>
        }
      >
        <Form
          form={form}
          layout="vertical"
          initialValues={{
            enabled: true,
            base_url: 'http://10.239.16.1:30000',
            username: 'admin',
            user_id: '1',
            effective_time: 7200,
            timeout: 5,
            area_type: 1,
            insecure: false,
          }}
        >
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(180px, 1fr))', gap: 16 }}>
            <Form.Item name="enabled" label="启用控制器集成" valuePropName="checked">
              <Switch checkedChildren="启用" unCheckedChildren="停用" />
            </Form.Item>
            <Form.Item name="base_url" label="北向 API 地址" rules={[{ required: true, message: '请输入控制器 API 地址' }]}>
              <Input placeholder="http://10.239.16.1:30000" />
            </Form.Item>
            <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}>
              <Input placeholder="admin" />
            </Form.Item>
            <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
              <Input.Password visibilityToggle={false} placeholder="保存后再次打开会隐藏" />
            </Form.Item>
            <Form.Item name="user_id" label="认证 ID">
              <Input placeholder="1" />
            </Form.Item>
            <Form.Item name="region_id" label="Region ID">
              <Input placeholder="可为空" />
            </Form.Item>
            <Form.Item name="effective_time" label="Token 有效期/秒">
              <Input type="number" />
            </Form.Item>
            <Form.Item name="timeout" label="请求超时/秒">
              <Input type="number" />
            </Form.Item>
            <Form.Item name="area_type" label="区域类型">
              <Select
                options={[
                  { value: 0, label: '0' },
                  { value: 1, label: '1' },
                  { value: 2, label: '2' },
                ]}
              />
            </Form.Item>
            <Form.Item name="insecure" label="HTTPS 证书校验" valuePropName="checked">
              <Switch checkedChildren="忽略" unCheckedChildren="校验" />
            </Form.Item>
          </div>
        </Form>
      </Card>

      <Card title="连通性测试结果">
        <Table<ControllerCheck>
          rowKey="name"
          size="small"
          dataSource={checks}
          pagination={false}
          locale={{ emptyText: '点击“测试连通性”后展示结果' }}
          columns={[
            { title: '检查项', dataIndex: 'name', width: 180 },
            {
              title: '结果',
              dataIndex: 'ok',
              width: 100,
              render: (value: boolean) => <Tag color={value ? 'success' : 'error'}>{value ? '通过' : '失败'}</Tag>,
            },
            { title: '耗时', dataIndex: 'elapsed_ms', width: 100, render: (value: number) => `${value}ms` },
            { title: '详情', dataIndex: 'detail', width: 260 },
            { title: '响应预览', dataIndex: 'preview', ellipsis: true, render: (value?: string) => value || '-' },
          ]}
        />
      </Card>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', gap: 16 }}>
        <Card
          title={`资产样例${assetTotal ? `（总数 ${assetTotal} 条，仅展示前 10 条）` : '（仅展示前 10 条）'}`}
          extra={<Button onClick={loadAssetSample} loading={assetsLoading}>读取样例</Button>}
        >
          <Table
            rowKey={(record) => record.id || record.ucAssetId || record.ip}
            size="small"
            loading={assetsLoading}
            dataSource={assets}
            pagination={false}
            scroll={{ y: 300 }}
            columns={[
              { title: '设备', dataIndex: 'name', ellipsis: true },
              { title: 'IP', dataIndex: 'ip', width: 130 },
              { title: '型号', dataIndex: 'model', width: 140, ellipsis: true },
              { title: '状态', dataIndex: 'status', width: 80, render: (value: number) => value === 1 ? <Tag color="success">在线</Tag> : <Tag>离线</Tag> },
            ]}
          />
        </Card>
        <Card
          title={`光模块样例${opticalTotal ? `（总数 ${opticalTotal} 条，仅展示前 10 条）` : '（仅展示前 10 条）'}`}
          extra={<Button onClick={loadOpticalSample} loading={opticalsLoading}>读取样例</Button>}
        >
          <Table
            rowKey={(record) => `${record.assetId || record.deviceIp}-${record.ifIndex || record.ifDesc}-${record.serialNumber || ''}`}
            size="small"
            loading={opticalsLoading}
            dataSource={opticals}
            pagination={false}
            scroll={{ y: 300 }}
            columns={[
              { title: '设备', dataIndex: 'deviceName', ellipsis: true },
              { title: 'IP', dataIndex: 'deviceIp', width: 120 },
              { title: '接口', dataIndex: 'ifDesc', width: 130, ellipsis: true },
              { title: '收光', dataIndex: 'curRxPower', width: 90, render: (value: number) => value ?? '-' },
              { title: '发光', dataIndex: 'curTxPower', width: 90, render: (value: number) => value ?? '-' },
            ]}
          />
        </Card>
      </div>
    </Space>
  )
}

const Settings = () => {
  const currentUser = useAuthStore((state) => state.user)
  const setCurrentUser = useAuthStore((state) => state.setUser)
  const [userForm] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [users, setUsers] = useState<User[]>([])
  const [menuOptions, setMenuOptions] = useState<Array<{ label: string; value: string }>>([])
  const [userModalOpen, setUserModalOpen] = useState(false)
  const [editingUser, setEditingUser] = useState<User | null>(null)
  const [auditLogs, setAuditLogs] = useState<AuditLog[]>([])
  const [auditTotal, setAuditTotal] = useState(0)
  const [auditPage, setAuditPage] = useState(1)
  const [auditPageSize, setAuditPageSize] = useState(20)
  const [auditLoading, setAuditLoading] = useState(false)
  const [auditFilters, setAuditFilters] = useState<Record<string, any>>({})
  const [auditDetail, setAuditDetail] = useState<AuditLog | null>(null)
  const isAdmin = Boolean(currentUser?.is_superuser)
  const canEditSelf = !currentUser?.read_only

  const fetchUsers = async () => {
    setLoading(true)
    try {
      if (isAdmin) {
        const [usersResult, menuResult] = await Promise.all([
          getUsers(),
          getMenuOptions(),
        ])
        setUsers(usersResult.items)
        setMenuOptions(menuResult.items)
      } else {
        setUsers(currentUser ? [currentUser] : [])
        setMenuOptions([])
      }
    } catch {
      message.error('获取系统设置失败')
    } finally {
      setLoading(false)
    }
  }

  const refreshUsersOnlineState = async () => {
    if (!isAdmin) return
    try {
      const usersResult = await getUsers()
      setUsers(usersResult.items)
    } catch {
      // 在线状态轮询失败时静默处理，避免反复弹错。
    }
  }

  useEffect(() => {
    fetchUsers()
  }, [isAdmin, currentUser?.id])

  useEffect(() => {
    if (!isAdmin) return
    const timer = window.setInterval(() => {
      refreshUsersOnlineState()
    }, 10000)
    return () => window.clearInterval(timer)
  }, [isAdmin])

  const fetchAuditLogs = async (
    nextPage = auditPage,
    nextPageSize = auditPageSize,
    filters = auditFilters
  ) => {
    setAuditLoading(true)
    try {
      const result = await getAuditLogs({
        skip: (nextPage - 1) * nextPageSize,
        limit: nextPageSize,
        username: filters.username?.trim() || undefined,
        menu: filters.menu?.trim() || undefined,
        action: filters.action || undefined,
        method: filters.method || undefined,
        path: filters.path?.trim() || undefined,
        success: filters.success,
      })
      setAuditLogs(result.items)
      setAuditTotal(result.total)
      setAuditPage(nextPage)
      setAuditPageSize(nextPageSize)
      setAuditFilters(filters)
    } catch {
      message.error('获取操作审计失败')
    } finally {
      setAuditLoading(false)
    }
  }

  const handleCreateUser = () => {
    setEditingUser(null)
    userForm.resetFields()
    userForm.setFieldsValue({
      is_active: true,
      read_only: false,
      allowed_menus: ['/dashboard', '/devices', '/port-query', '/ip-flow-query', '/device-overview'],
    })
    setUserModalOpen(true)
  }

  const handleEditUser = (user: User) => {
    setEditingUser(user)
    userForm.setFieldsValue({
      ...user,
      password: undefined,
    })
    setUserModalOpen(true)
  }

  const handleSaveUser = async () => {
    try {
      const values = await userForm.validateFields()
      setSaving(true)
      if (editingUser) {
        if (isAdmin && editingUser.id !== currentUser?.id) {
          await updateUser(editingUser.id, values)
        } else if (isAdmin) {
          await updateUser(editingUser.id, values)
        } else {
          const updatedUser = await updateCurrentUser(values)
          setCurrentUser(updatedUser)
        }
        message.success('用户更新成功')
      } else {
        await createUser(values)
        message.success('用户创建成功')
      }
      setUserModalOpen(false)
      fetchUsers()
    } catch (error: any) {
      if (!error?.errorFields) {
        message.error(error?.response?.data?.detail || '保存失败')
      }
    } finally {
      setSaving(false)
    }
  }

  const handleDeleteUser = async (id: number) => {
    try {
      await deleteUser(id)
      message.success('用户删除成功')
      fetchUsers()
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '删除失败')
    }
  }

  return (
    <>
      {!isAdmin ? (
        <Card
          title="个人中心"
          extra={canEditSelf ? (
            <Tooltip title="编辑自己的信息">
              <Button type="primary" icon={<EditOutlined />} onClick={() => currentUser && handleEditUser(currentUser)}>
                修改信息
              </Button>
            </Tooltip>
          ) : null}
        >
          <Space direction="vertical" size={12}>
            <Typography.Text>账号：{currentUser?.username || '-'}</Typography.Text>
            <Typography.Text>姓名：{currentUser?.full_name || '-'}</Typography.Text>
            <Typography.Text>邮箱：{currentUser?.email || '-'}</Typography.Text>
            <Typography.Text>电话：{currentUser?.phone || '-'}</Typography.Text>
            <Typography.Text>部门：{currentUser?.department || '-'}</Typography.Text>
            <Typography.Text>
              最后登录时间：{currentUser?.last_login ? new Date(currentUser.last_login).toLocaleString() : '-'}
            </Typography.Text>
          </Space>
        </Card>
      ) : (
      <Tabs
        defaultActiveKey="users"
        items={[
          {
            key: 'users',
            label: '用户管理',
            children: (
              <Card
                title="用户管理"
                extra={
                  <Space>
                    <Button icon={<ReloadOutlined />} onClick={fetchUsers} loading={loading}>
                      刷新
                    </Button>
                    <Button type="primary" icon={<PlusOutlined />} onClick={handleCreateUser}>
                      新增账号
                    </Button>
                  </Space>
                }
              >
                <Table<User>
                  rowKey="id"
                  loading={loading}
                  dataSource={users}
                  pagination={tablePagination}
                  columns={[
                    { title: '用户名', dataIndex: 'username', key: 'username' },
                    { title: '姓名', dataIndex: 'full_name', key: 'full_name', render: (v: string) => v || '-' },
                    { title: '邮箱', dataIndex: 'email', key: 'email' },
                    {
                      title: '权限模式',
                      key: 'read_only',
                      render: (_: unknown, record: User) => (
                        <Tag color={record.read_only ? 'warning' : 'success'}>
                          {record.read_only ? '只读' : '可编辑'}
                        </Tag>
                      ),
                    },
                    {
                      title: '状态',
                      dataIndex: 'is_active',
                      key: 'is_active',
                      render: (value: boolean) => (
                        <Tag color={value ? 'success' : 'default'}>{value ? '启用' : '停用'}</Tag>
                      ),
                    },
                    {
                      title: '最后登录时间',
                      dataIndex: 'last_login',
                      key: 'last_login',
                      render: (value?: string | null) => value ? new Date(value).toLocaleString() : '-',
                    },
                    {
                      title: '在线状态',
                      dataIndex: 'online',
                      key: 'online',
                      render: (value?: boolean) => (
                        <Tag color={value ? 'success' : 'default'}>{value ? '在线' : '离线'}</Tag>
                      ),
                    },
                    {
                      title: '最后离线时间',
                      dataIndex: 'last_offline_at',
                      key: 'last_offline_at',
                      render: (value?: string | null, record?: User) => record?.online ? '-' : (value ? new Date(value).toLocaleString() : '-'),
                    },
                    {
                      title: '可访问菜单',
                      dataIndex: 'allowed_menus',
                      key: 'allowed_menus',
                      render: (menus: string[]) => menus?.length || 0,
                    },
                    {
                      title: '操作',
                      key: 'action',
                      render: (_: unknown, record: User) => (
                        <Space>
                          <Tooltip title="编辑">
                            <Button type="text" icon={<EditOutlined />} onClick={() => handleEditUser(record)} />
                          </Tooltip>
                          <Popconfirm
                            title="确认删除该账号吗？"
                            onConfirm={() => handleDeleteUser(record.id)}
                            disabled={record.id === currentUser?.id}
                          >
                            <Tooltip title="删除">
                              <Button
                                type="text"
                                danger
                                icon={<DeleteOutlined />}
                                disabled={record.id === currentUser?.id}
                              />
                            </Tooltip>
                          </Popconfirm>
                        </Space>
                      ),
                    },
                  ]}
                />
              </Card>
            ),
          },
          {
            key: 'audit',
            label: '操作审计',
            children: (
              <Card
                title="操作审计"
                extra={
                  <Button
                    icon={<ReloadOutlined />}
                    onClick={() => fetchAuditLogs(auditPage, auditPageSize, auditFilters)}
                  >
                    刷新
                  </Button>
                }
              >
                <Form
                  layout="inline"
                  style={{ marginBottom: 16, rowGap: 12 }}
                  onValuesChange={(_, values) => fetchAuditLogs(1, auditPageSize, values)}
                >
                  <Form.Item name="username">
                    <Input allowClear placeholder="账号" style={{ width: 140 }} />
                  </Form.Item>
                  <Form.Item name="menu">
                    <Input allowClear placeholder="菜单" style={{ width: 140 }} />
                  </Form.Item>
                  <Form.Item name="action">
                    <Select
                      allowClear
                      placeholder="动作"
                      style={{ width: 120 }}
                      options={[
                        { value: 'view', label: '查看' },
                        { value: 'create', label: '创建' },
                        { value: 'update', label: '修改' },
                        { value: 'delete', label: '删除' },
                        { value: 'login', label: '登录' },
                      ]}
                    />
                  </Form.Item>
                  <Form.Item name="method">
                    <Select
                      allowClear
                      placeholder="方法"
                      style={{ width: 110 }}
                      options={['GET', 'POST', 'PUT', 'PATCH', 'DELETE'].map((value) => ({ value, label: value }))}
                    />
                  </Form.Item>
                  <Form.Item name="success">
                    <Select
                      allowClear
                      placeholder="结果"
                      style={{ width: 110 }}
                      options={[
                        { value: true, label: '成功' },
                        { value: false, label: '失败' },
                      ]}
                    />
                  </Form.Item>
                  <Form.Item name="path">
                    <Input allowClear placeholder="接口路径" style={{ width: 220 }} />
                  </Form.Item>
                </Form>
                <Table
                  rowKey="id"
                  loading={auditLoading}
                  dataSource={auditLogs}
                  pagination={{
                    current: auditPage,
                    pageSize: auditPageSize,
                    total: auditTotal,
                    showSizeChanger: true,
                    pageSizeOptions: [10, 20, 50, 100],
                    showTotal: (count, range) => `第 ${range[0]}-${range[1]} 条 / 共 ${count} 条`,
                    onChange: (page, pageSize) => fetchAuditLogs(page, pageSize, auditFilters),
                  }}
                  columns={[
                    { title: '时间', dataIndex: 'created_at', width: 180, render: (value?: string) => value ? new Date(value).toLocaleString() : '-' },
                    { title: '账号', dataIndex: 'username', width: 120 },
                    { title: '菜单', dataIndex: 'menu', width: 120, render: (value?: string) => value || '-' },
                    { title: '动作', dataIndex: 'action', width: 90, render: (value: string) => <Tag>{value}</Tag> },
                    { title: '方法', dataIndex: 'method', width: 90 },
                    { title: '路径', dataIndex: 'path', ellipsis: true },
                    { title: '状态码', dataIndex: 'response_status', width: 90 },
                    {
                      title: '结果',
                      dataIndex: 'success',
                      width: 90,
                      render: (value: boolean) => <Tag color={value ? 'success' : 'error'}>{value ? '成功' : '失败'}</Tag>,
                    },
                    { title: '来源IP', dataIndex: 'client_ip', width: 130, render: (value?: string) => value || '-' },
                    {
                      title: '详情',
                      width: 80,
                      render: (_: unknown, record: AuditLog) => (
                        <Tooltip title="查看详情">
                          <Button type="text" icon={<EyeOutlined />} onClick={() => setAuditDetail(record)} />
                        </Tooltip>
                      ),
                    },
                  ]}
                />
              </Card>
            ),
          },
          {
            key: 'controller',
            label: '控制器集成',
            children: <ControllerIntegrationPanel />,
          },
        ]}
        onChange={(key) => {
          if (key === 'audit' && !auditLogs.length) {
            fetchAuditLogs(1, auditPageSize, auditFilters)
          }
        }}
      />
      )}

      <Modal
        title={editingUser ? '编辑账号' : '新增账号'}
        open={userModalOpen}
        onCancel={() => setUserModalOpen(false)}
        onOk={handleSaveUser}
        confirmLoading={saving}
        destroyOnClose
        width={720}
      >
        <Form form={userForm} layout="vertical">
          <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}>
            <Input />
          </Form.Item>
          <Form.Item name="full_name" label="姓名">
            <Input />
          </Form.Item>
          <Form.Item name="email" label="邮箱" rules={[{ required: true, message: '请输入邮箱' }]}>
            <Input />
          </Form.Item>
          <Form.Item name="phone" label="电话">
            <Input />
          </Form.Item>
          <Form.Item name="department" label="部门">
            <Input />
          </Form.Item>
          <Form.Item
            name="password"
            label={editingUser ? '新密码（留空则不修改）' : '密码'}
            rules={editingUser ? [] : [{ required: true, message: '请输入密码' }]}
          >
            <Input.Password visibilityToggle={false} />
          </Form.Item>
          {isAdmin ? (
            <>
              <Form.Item name="allowed_menus" label="可访问菜单" rules={[{ required: true, message: '请选择可访问菜单' }]}>
                <Select mode="multiple" options={menuOptions} />
              </Form.Item>
              <Form.Item name="is_active" label="启用状态" valuePropName="checked">
                <Switch />
              </Form.Item>
              <Form.Item name="read_only" label="只读账号" valuePropName="checked">
                <Switch />
              </Form.Item>
            </>
          ) : null}
        </Form>
      </Modal>
      <Modal
        title="操作审计详情"
        open={Boolean(auditDetail)}
        onCancel={() => setAuditDetail(null)}
        footer={<Button onClick={() => setAuditDetail(null)}>关闭</Button>}
        width={760}
      >
        <Typography.Paragraph copyable>
          {auditDetail ? JSON.stringify(auditDetail, null, 2) : ''}
        </Typography.Paragraph>
      </Modal>
    </>
  )
}

export default Settings
