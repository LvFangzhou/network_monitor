import { useEffect, useState } from 'react'
import { Button, Card, Form, Input, InputNumber, Modal, Popconfirm, Select, Space, Table, Tag, Tooltip, message } from 'antd'
import { DeleteOutlined, EditOutlined, PlusOutlined } from '@ant-design/icons'
import { getDatacenters, type Datacenter } from '../../api/devices'
import { createIPRecord, deleteIPRecord, getCircuits, getIPRecords, updateIPRecord, type Circuit, type IPRecord } from '../../api/resources'
import { useAuthStore } from '../../store/auth'

const tablePagination = {
  defaultPageSize: 20,
  showSizeChanger: true,
  pageSizeOptions: [10, 20, 50, 100],
  showTotal: (total: number, range: [number, number]) => `第 ${range[0]}-${range[1]} 条 / 共 ${total} 条`,
}

const IPDBList = () => {
  const [form] = Form.useForm()
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [open, setOpen] = useState(false)
  const [records, setRecords] = useState<IPRecord[]>([])
  const [datacenters, setDatacenters] = useState<Datacenter[]>([])
  const [circuits, setCircuits] = useState<Circuit[]>([])
  const [editingRecord, setEditingRecord] = useState<IPRecord | null>(null)
  const [search, setSearch] = useState('')
  const canModify = !useAuthStore((state) => state.user?.read_only)

  const fetchOptions = async () => {
    const [datacenterResult, circuitResult] = await Promise.all([getDatacenters(), getCircuits()])
    setDatacenters(datacenterResult)
    setCircuits(circuitResult.items)
  }

  const fetchRecords = async (keyword = search) => {
    setLoading(true)
    try {
      const result = await getIPRecords(keyword || undefined)
      setRecords(result.items)
    } catch {
      message.error('获取IPDB列表失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchOptions()
    fetchRecords('')
  }, [])

  useEffect(() => {
    const timer = window.setTimeout(() => {
      fetchRecords(search)
    }, 250)
    return () => window.clearTimeout(timer)
  }, [search])

  const handleCreate = () => {
    setEditingRecord(null)
    form.resetFields()
    form.setFieldsValue({ prefix_length: 32, status: 'allocated', usage_type: 'business' })
    setOpen(true)
  }

  const handleEdit = (record: IPRecord) => {
    setEditingRecord(record)
    form.setFieldsValue(record)
    setOpen(true)
  }

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields()
      setSaving(true)
      if (editingRecord) {
        await updateIPRecord(editingRecord.id, values)
        message.success('更新成功')
      } else {
        await createIPRecord(values)
        message.success('创建成功')
      }
      setOpen(false)
      fetchRecords()
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
      await deleteIPRecord(id)
      message.success('删除成功')
      fetchRecords()
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '删除失败')
    }
  }

  return (
    <Card
      title="IPDB"
      extra={
        <Space>
          <Input
            allowClear
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="搜索IP、CIDR、机房、线路、用途"
            style={{ width: 300 }}
          />
          {canModify ? (
            <Tooltip title="新增IP">
              <Button type="primary" icon={<PlusOutlined />} onClick={handleCreate}>新增IP</Button>
            </Tooltip>
          ) : null}
        </Space>
      }
    >
      <Table
        rowKey="id"
        loading={loading}
        dataSource={records}
        pagination={tablePagination}
        columns={[
          {
            title: 'IP地址',
            key: 'ip',
            render: (_: unknown, record: IPRecord) => `${record.ip_address}/${record.prefix_length}`,
          },
          { title: '所属机房', dataIndex: 'datacenter_name', key: 'datacenter_name', render: (v: string) => v || '-' },
          { title: '所属线路', dataIndex: 'circuit_name', key: 'circuit_name', render: (v: string) => v || '-' },
          { title: '用途', dataIndex: 'usage_type', key: 'usage_type' },
          {
            title: '状态',
            dataIndex: 'status',
            key: 'status',
            render: (v: string) => {
              const colorMap: Record<string, string> = {
                allocated: 'processing',
                available: 'success',
                reserved: 'warning',
              }
              const labelMap: Record<string, string> = {
                allocated: '已分配',
                available: '可用',
                reserved: '预留',
              }
              return <Tag color={colorMap[v] || 'default'}>{labelMap[v] || v}</Tag>
            },
          },
          { title: '备注', dataIndex: 'description', key: 'description', render: (v: string) => v || '-' },
          {
            title: '操作',
            key: 'action',
            render: (_: unknown, record: IPRecord) => (
              <Space>
                <Tooltip title="编辑">
                  <Button type="text" icon={<EditOutlined />} onClick={() => handleEdit(record)} />
                </Tooltip>
                <Popconfirm title="确认删除该IP记录吗？" onConfirm={() => handleDelete(record.id)}>
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
        title={editingRecord ? '编辑IP记录' : '新增IP记录'}
        open={open}
        onCancel={() => setOpen(false)}
        onOk={handleSubmit}
        confirmLoading={saving}
        destroyOnClose
        width={720}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="ip_address" label="IP地址" rules={[{ required: true, message: '请输入IP地址' }]}>
            <Input placeholder="例如：203.0.113.10" />
          </Form.Item>
          <Form.Item name="prefix_length" label="前缀长度">
            <InputNumber min={0} max={128} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item name="datacenter_id" label="所属机房">
            <Select allowClear options={datacenters.map((item) => ({ value: item.id, label: item.name }))} />
          </Form.Item>
          <Form.Item name="circuit_id" label="所属线路">
            <Select allowClear options={circuits.map((item) => ({ value: item.id, label: item.name }))} />
          </Form.Item>
          <Form.Item name="usage_type" label="用途">
            <Select
              options={[
                { value: 'business', label: '业务使用' },
                { value: 'management', label: '管理地址' },
                { value: 'nat', label: 'NAT/出口' },
                { value: 'reserve', label: '预留' },
              ]}
            />
          </Form.Item>
          <Form.Item name="status" label="状态">
            <Select
              options={[
                { value: 'allocated', label: '已分配' },
                { value: 'available', label: '可用' },
                { value: 'reserved', label: '预留' },
              ]}
            />
          </Form.Item>
          <Form.Item name="description" label="备注">
            <Input.TextArea rows={3} placeholder="可填写归属系统、使用说明等" />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}

export default IPDBList
