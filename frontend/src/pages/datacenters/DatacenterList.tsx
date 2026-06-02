import { useEffect, useState } from 'react'
import {
  Button,
  Card,
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
import { DeleteOutlined, EditOutlined, PlusOutlined } from '@ant-design/icons'
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

const DatacenterList = () => {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [open, setOpen] = useState(false)
  const [datacenters, setDatacenters] = useState<Datacenter[]>([])
  const [editingDatacenter, setEditingDatacenter] = useState<Datacenter | null>(null)
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

  return (
    <Card
      title="机房管理"
      extra={canModify ? (
        <Tooltip title="新增机房">
          <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>
            新增机房
          </Button>
        </Tooltip>
      ) : null}
    >
      <Table
        rowKey="id"
        loading={loading}
        dataSource={datacenters}
        pagination={tablePagination}
        columns={[
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
            hidden: !canModify,
          },
        ]}
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
    </Card>
  )
}

export default DatacenterList
