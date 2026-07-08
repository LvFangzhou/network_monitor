import { useEffect, useState } from 'react'
import { Button, Card, Form, Input, Modal, Popconfirm, Select, Space, Switch, Table, Tag, Tooltip, message } from 'antd'
import { DeleteOutlined, EditOutlined, PlusOutlined } from '@ant-design/icons'
import { createVendor, deleteVendor, getVendors, updateVendor, type Vendor } from '../../api/resources'
import { useAuthStore } from '../../store/auth'

const vendorTypeOptions = [
  { value: 'dedicated', label: '专线供应商' },
  { value: 'internet', label: '互联网供应商' },
  { value: 'other', label: '其他' },
]

const tablePagination = {
  defaultPageSize: 20,
  showSizeChanger: true,
  pageSizeOptions: [10, 20, 50, 100],
  showTotal: (total: number, range: [number, number]) => `第 ${range[0]}-${range[1]} 条 / 共 ${total} 条`,
}

const VendorList = ({ embedded = false }: { embedded?: boolean }) => {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [open, setOpen] = useState(false)
  const [vendors, setVendors] = useState<Vendor[]>([])
  const [editingVendor, setEditingVendor] = useState<Vendor | null>(null)
  const canModify = !useAuthStore((state) => state.user?.read_only)

  const fetchVendors = async () => {
    setLoading(true)
    try {
      const result = await getVendors()
      setVendors(result.items)
    } catch {
      message.error('获取供应商列表失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchVendors()
  }, [])

  const handleCreate = () => {
    setEditingVendor(null)
    form.resetFields()
    form.setFieldsValue({ vendor_type: 'other', is_active: true })
    setOpen(true)
  }

  const handleEdit = (record: Vendor) => {
    setEditingVendor(record)
    form.setFieldsValue(record)
    setOpen(true)
  }

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields()
      setSaving(true)
      if (editingVendor) {
        await updateVendor(editingVendor.id, values)
        message.success('更新成功')
      } else {
        await createVendor(values)
        message.success('创建成功')
      }
      setOpen(false)
      fetchVendors()
    } catch (error: any) {
      if (!error?.errorFields) {
        message.error(error?.response?.data?.detail || '保存失败')
      }
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (id: number) => {
    try {
      await deleteVendor(id)
      message.success('删除成功')
      fetchVendors()
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '删除失败')
    }
  }

  const content = (
    <>
      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 12 }}>
        {canModify ? (
          <Tooltip title="新增供应商">
            <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>新增供应商</Button>
          </Tooltip>
        ) : null}
      </div>
      <Table
        rowKey="id"
        loading={loading}
        dataSource={vendors}
        pagination={tablePagination}
        columns={[
          { title: '供应商名称', dataIndex: 'name', key: 'name' },
          {
            title: '类型',
            dataIndex: 'vendor_type',
            key: 'vendor_type',
            render: (value: string) => vendorTypeOptions.find((item) => item.value === value)?.label || value,
          },
          { title: '联系人', dataIndex: 'contact_person', key: 'contact_person', render: (v: string) => v || '-' },
          { title: '联系电话', dataIndex: 'contact_phone', key: 'contact_phone', render: (v: string) => v || '-' },
          { title: '联系邮箱', dataIndex: 'contact_email', key: 'contact_email', render: (v: string) => v || '-' },
          { title: '服务范围', dataIndex: 'service_scope', key: 'service_scope', render: (v: string) => v || '-' },
          {
            title: '状态',
            dataIndex: 'is_active',
            key: 'is_active',
            render: (value: boolean) => <Tag color={value ? 'success' : 'default'}>{value ? '启用' : '停用'}</Tag>,
          },
          {
            title: '操作',
            key: 'action',
            render: (_: unknown, record: Vendor) => (
              <Space>
                <Tooltip title="编辑">
                  <Button type="text" icon={<EditOutlined />} onClick={() => handleEdit(record)} />
                </Tooltip>
                <Popconfirm title="确认删除该供应商吗？" onConfirm={() => handleDelete(record.id)}>
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
        title={editingVendor ? '编辑供应商' : '新增供应商'}
        open={open}
        onCancel={() => setOpen(false)}
        onOk={handleSubmit}
        confirmLoading={saving}
        destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="供应商名称" rules={[{ required: true, message: '请输入供应商名称' }]}>
            <Input />
          </Form.Item>
          <Form.Item name="vendor_type" label="类型" rules={[{ required: true, message: '请选择类型' }]}>
            <Select options={vendorTypeOptions} />
          </Form.Item>
          <Form.Item name="contact_person" label="联系人">
            <Input />
          </Form.Item>
          <Form.Item name="contact_phone" label="联系电话">
            <Input />
          </Form.Item>
          <Form.Item name="contact_email" label="联系邮箱">
            <Input />
          </Form.Item>
          <Form.Item name="service_scope" label="服务范围">
            <Input placeholder="例如：IDC出口、互联网宽带、专线接入" />
          </Form.Item>
          <Form.Item name="description" label="备注">
            <Input.TextArea rows={3} />
          </Form.Item>
          <Form.Item name="is_active" label="启用状态" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </>
  )

  if (embedded) {
    return content
  }

  return <Card title="供应商管理">{content}</Card>
}

export default VendorList
