import { useEffect, useState } from 'react'
import {
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
import { useAuthStore } from '../store/auth'

const tablePagination = {
  defaultPageSize: 20,
  showSizeChanger: true,
  pageSizeOptions: [10, 20, 50, 100],
  showTotal: (total: number, range: [number, number]) => `第 ${range[0]}-${range[1]} 条 / 共 ${total} 条`,
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
