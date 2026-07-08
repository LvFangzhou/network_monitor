import { useEffect, useState } from 'react'
import {
  Button,
  Card,
  Checkbox,
  Dropdown,
  Form,
  Input,
  Modal,
  Popconfirm,
  Space,
  Switch,
  Table,
  Tag,
  Tooltip,
  message,
  DatePicker,
} from 'antd'
import dayjs from 'dayjs'
import { DeleteOutlined, EditOutlined, PlusOutlined, SettingOutlined } from '@ant-design/icons'
import {
  createDatacenter,
  deleteDatacenter,
  getDatacenters,
  updateDatacenter,
  type Datacenter,
} from '../../api/devices'
import { useAuthStore } from '../../store/auth'

const tablePagination = {
  defaultPageSize: 20,
  showSizeChanger: true,
  pageSizeOptions: [10, 20, 50, 100],
  showTotal: (total: number, range: [number, number]) => `第 ${range[0]}-${range[1]} 条 / 共 ${total} 条`,
}

const VISIBLE_COLUMNS_STORAGE_KEY = 'datacenter-visible-columns-v3'
const defaultVisibleColumns = [
  'name',
  'code',
  'location',
  'address',
  'contact_person',
  'contact_phone',
  'network_owner',
  'network_owner_email',
  'build_date',
  'is_active',
]

const DatacenterList = ({ embedded = false }: { embedded?: boolean }) => {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [open, setOpen] = useState(false)
  const [datacenters, setDatacenters] = useState<Datacenter[]>([])
  const [editingDatacenter, setEditingDatacenter] = useState<Datacenter | null>(null)
  const [visibleColumns, setVisibleColumns] = useState<string[]>(() => {
    try {
      const storedColumns = window.localStorage.getItem(VISIBLE_COLUMNS_STORAGE_KEY)
      if (storedColumns) {
        const parsed = JSON.parse(storedColumns)
        if (Array.isArray(parsed)) {
          return parsed
        }
      }
    } catch {
      // Ignore invalid localStorage values and use the default column set.
    }
    return defaultVisibleColumns
  })
  const canModify = !useAuthStore((state) => state.user?.read_only)

  const fetchDatacenters = async () => {
    setLoading(true)
    try {
      const result = await getDatacenters()
      setDatacenters(result)
    } catch (error) {
      message.error('获取机房列表失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchDatacenters()
  }, [])

  useEffect(() => {
    window.localStorage.setItem(VISIBLE_COLUMNS_STORAGE_KEY, JSON.stringify(visibleColumns))
  }, [visibleColumns])

  const handleCreate = () => {
    setEditingDatacenter(null)
    form.resetFields()
    form.setFieldsValue({ is_active: true })
    setOpen(true)
  }

  const handleEdit = (datacenter: Datacenter) => {
    setEditingDatacenter(datacenter)
    form.setFieldsValue({
      ...datacenter,
      build_date: datacenter.build_date ? dayjs(datacenter.build_date) : null,
    })
    setOpen(true)
  }

  const handleDelete = async (id: number) => {
    try {
      await deleteDatacenter(id)
      message.success('删除成功')
      fetchDatacenters()
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '删除失败')
    }
  }

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields()
      setSaving(true)

      const payload = {
        ...values,
        build_date: values.build_date ? values.build_date.toISOString() : null,
      }

      if (editingDatacenter) {
        await updateDatacenter(editingDatacenter.id, payload)
        message.success('更新成功')
      } else {
        await createDatacenter(payload)
        message.success('创建成功')
      }

      setOpen(false)
      form.resetFields()
      fetchDatacenters()
    } catch (error: any) {
      if (!error?.errorFields) {
        message.error(error?.response?.data?.detail || '保存失败')
      }
    } finally {
      setSaving(false)
    }
  }

  const allColumns = [
    {
      title: '机房名称',
      dataIndex: 'name',
      key: 'name',
    },
    {
      title: '机房编号',
      dataIndex: 'code',
      key: 'code',
      render: (value: string) => value || '-',
    },
    {
      title: '位置',
      dataIndex: 'location',
      key: 'location',
      render: (value: string) => value || '-',
    },
    {
      title: '详细地址',
      dataIndex: 'address',
      key: 'address',
      render: (value: string) => value || '-',
    },
    {
      title: '负责人',
      dataIndex: 'contact_person',
      key: 'contact_person',
      render: (value: string) => value || '-',
    },
    {
      title: '负责人电话',
      dataIndex: 'contact_phone',
      key: 'contact_phone',
      render: (value: string) => value || '-',
    },
    {
      title: '负责人邮箱',
      dataIndex: 'contact_email',
      key: 'contact_email',
      render: (value: string) => value || '-',
    },
    {
      title: '网络负责人',
      dataIndex: 'network_owner',
      key: 'network_owner',
      render: (value: string) => value || '-',
    },
    {
      title: '网络负责人邮箱',
      dataIndex: 'network_owner_email',
      key: 'network_owner_email',
      render: (value: string) => value || '-',
    },
    {
      title: '建设时间',
      dataIndex: 'build_date',
      key: 'build_date',
      render: (value: string) => (value ? dayjs(value).format('YYYY-MM-DD') : '-'),
    },
    {
      title: '状态',
      dataIndex: 'is_active',
      key: 'is_active',
      render: (value: boolean) => (
        <Tag color={value ? 'success' : 'default'}>
          {value ? '启用' : '停用'}
        </Tag>
      ),
    },
    {
      title: '操作',
      key: 'action',
      render: (_: unknown, record: Datacenter) => (
        <Space>
          <Tooltip title="编辑">
            <Button type="text" icon={<EditOutlined />} onClick={() => handleEdit(record)} />
          </Tooltip>
          <Popconfirm
            title="确认删除该机房吗？"
            onConfirm={() => handleDelete(record.id)}
            okText="删除"
            cancelText="取消"
          >
            <Tooltip title="删除">
              <Button type="text" danger icon={<DeleteOutlined />} />
            </Tooltip>
          </Popconfirm>
        </Space>
      ),
    },
  ]

  const tableColumns = allColumns.filter((column) => {
    if (column.key === 'action') {
      return canModify
    }
    return visibleColumns.includes(String(column.key))
  })

  const content = (
    <>
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 12 }}>
        <Space>
          <Dropdown
            trigger={['click']}
            menu={{
              items: [
                {
                  key: 'visible-columns',
                  label: (
                    <Checkbox.Group
                      value={visibleColumns}
                      onChange={(values) => setVisibleColumns(values as string[])}
                      style={{ display: 'grid', rowGap: 8 }}
                    >
                      {allColumns
                        .filter((column) => column.key !== 'action')
                        .map((column) => (
                          <Checkbox key={String(column.key)} value={String(column.key)}>
                            {String(column.title)}
                          </Checkbox>
                        ))}
                    </Checkbox.Group>
                  ),
                },
              ],
            }}
          >
            <Tooltip title="显示或隐藏列">
              <Button icon={<SettingOutlined />}>显示/隐藏列</Button>
            </Tooltip>
          </Dropdown>
          {canModify ? (
            <Tooltip title="新增机房">
              <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>
                新增机房
              </Button>
            </Tooltip>
          ) : null}
        </Space>
      </div>
      <Table
        rowKey="id"
        loading={loading}
        dataSource={datacenters}
        pagination={tablePagination}
        columns={tableColumns}
      />

      <Modal
        title={editingDatacenter ? '编辑机房' : '新增机房'}
        open={open}
        onCancel={() => setOpen(false)}
        onOk={handleSubmit}
        confirmLoading={saving}
        destroyOnClose
        width={720}
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="name"
            label="机房名称"
            rules={[{ required: true, message: '请输入机房名称' }]}
          >
            <Input placeholder="例如：北京一号机房" />
          </Form.Item>

          <Form.Item
            name="code"
            label="机房编号"
          >
            <Input placeholder="例如：IDC-BJ-01" />
          </Form.Item>

          <Form.Item
            name="location"
            label="位置"
          >
            <Input placeholder="例如：北京市朝阳区" />
          </Form.Item>

          <Form.Item
            name="address"
            label="详细地址"
          >
            <Input placeholder="例如：xx路xx号xx园区" />
          </Form.Item>

          <Form.Item
            name="contact_person"
            label="负责人名称"
          >
            <Input placeholder="例如：张三" />
          </Form.Item>

          <Form.Item
            name="contact_phone"
            label="负责人电话"
          >
            <Input placeholder="例如：13800138000" />
          </Form.Item>

          <Form.Item
            name="contact_email"
            label="负责人邮箱"
          >
            <Input placeholder="例如：admin@example.com" />
          </Form.Item>

          <Form.Item
            name="network_owner"
            label="网络负责人"
            tooltip="用于标记该机房网络问题默认负责人，后续机器人通告可以按机房带出。"
          >
            <Input placeholder="例如：张三 / 网络一组" />
          </Form.Item>

          <Form.Item
            name="network_owner_email"
            label="网络负责人邮箱"
            tooltip="用于后续机器人通告或邮件通知中带出该机房网络负责人的邮箱；多人用英文逗号分隔。"
          >
            <Input placeholder="例如：zhangsan@example.com,lisi@example.com" />
          </Form.Item>

          <Form.Item
            name="build_date"
            label="建设时间"
          >
            <DatePicker style={{ width: '100%' }} />
          </Form.Item>

          <Form.Item
            name="description"
            label="备注"
          >
            <Input.TextArea rows={3} placeholder="可填写容量、用途、冗余信息等" />
          </Form.Item>

          <Form.Item
            name="is_active"
            label="启用状态"
            valuePropName="checked"
          >
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )

  if (embedded) {
    return content
  }

  return <Card title="机房管理">{content}</Card>
}

export default DatacenterList
