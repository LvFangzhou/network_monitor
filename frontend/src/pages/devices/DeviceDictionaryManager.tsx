import { useEffect, useState } from 'react'
import { Button, Card, Form, Input, Modal, Popconfirm, Space, Switch, Table, Tabs, Tooltip, message } from 'antd'
import {
  getDeviceTypesList,
  createDeviceType,
  updateDeviceType,
  deleteDeviceType,
  getDeviceRolesList,
  createDeviceRole,
  updateDeviceRole,
  deleteDeviceRole,
  getDeviceVendorsList,
  createDeviceVendor,
  updateDeviceVendor,
  deleteDeviceVendor,
} from '../../api/devices'
import type { DeviceRole, DeviceType, DeviceVendor } from '../../api/devices'
import { useAuthStore } from '../../store/auth'

type CatalogTab = 'deviceType' | 'deviceRole' | 'deviceVendor'

const DeviceDictionaryManager = () => {
  const [deviceTypeCatalogs, setDeviceTypeCatalogs] = useState<DeviceType[]>([])
  const [deviceRoleCatalogs, setDeviceRoleCatalogs] = useState<DeviceRole[]>([])
  const [deviceVendorCatalogs, setDeviceVendorCatalogs] = useState<DeviceVendor[]>([])
  const [catalogTab, setCatalogTab] = useState<CatalogTab>('deviceRole')
  const [catalogEditorOpen, setCatalogEditorOpen] = useState(false)
  const [catalogSaving, setCatalogSaving] = useState(false)
  const [catalogEditingItem, setCatalogEditingItem] = useState<DeviceType | DeviceRole | DeviceVendor | null>(null)
  const [catalogForm] = Form.useForm()
  const canModify = !useAuthStore((state) => state.user?.read_only)

  const fetchCatalogs = async () => {
    try {
      const [deviceTypesResult, deviceRolesResult, deviceVendorsResult] = await Promise.all([
        getDeviceTypesList(),
        getDeviceRolesList(),
        getDeviceVendorsList(),
      ])
      setDeviceTypeCatalogs(deviceTypesResult)
      setDeviceRoleCatalogs(deviceRolesResult)
      setDeviceVendorCatalogs(deviceVendorsResult)
    } catch (error) {
      console.error('获取字典选项失败:', error)
      message.error('获取字典数据失败')
    }
  }

  useEffect(() => {
    fetchCatalogs()
  }, [])

  const getCatalogMeta = () => {
    if (catalogTab === 'deviceType') {
      return {
        title: '设备类型',
        items: deviceTypeCatalogs,
        create: createDeviceType,
        update: updateDeviceType,
        remove: deleteDeviceType,
      }
    }
    if (catalogTab === 'deviceRole') {
      return {
        title: '设备角色',
        items: deviceRoleCatalogs,
        create: createDeviceRole,
        update: updateDeviceRole,
        remove: deleteDeviceRole,
      }
    }
    return {
      title: '设备厂商',
      items: deviceVendorCatalogs,
      create: createDeviceVendor,
      update: updateDeviceVendor,
      remove: deleteDeviceVendor,
    }
  }

  const openCatalogEditor = (item?: DeviceType | DeviceRole | DeviceVendor) => {
    setCatalogEditingItem(item || null)
    catalogForm.resetFields()
    catalogForm.setFieldsValue(item || { is_active: true })
    setCatalogEditorOpen(true)
  }

  const handleSaveCatalog = async () => {
    const meta = getCatalogMeta()
    try {
      const values = await catalogForm.validateFields()
      setCatalogSaving(true)
      if (catalogEditingItem) {
        await meta.update(catalogEditingItem.id, values)
        message.success(`${meta.title}更新成功`)
      } else {
        await meta.create(values)
        message.success(`${meta.title}创建成功`)
      }
      setCatalogEditorOpen(false)
      await fetchCatalogs()
    } catch (error: any) {
      if (!error?.errorFields) {
        message.error(error?.response?.data?.detail || '保存失败')
      }
    } finally {
      setCatalogSaving(false)
    }
  }

  const handleDeleteCatalog = async (id: number) => {
    const meta = getCatalogMeta()
    try {
      await meta.remove(id)
      message.success(`${meta.title}删除成功`)
      await fetchCatalogs()
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '删除失败')
    }
  }

  return (
    <Card title="字典管理">
      <Tabs
        activeKey={catalogTab}
        onChange={(key) => setCatalogTab(key as CatalogTab)}
        items={[
          {
            key: 'deviceRole',
            label: '设备角色',
            children: (
              <Table
                rowKey="id"
                dataSource={deviceRoleCatalogs}
                pagination={false}
                columns={[
                  { title: '名称', dataIndex: 'name', key: 'name' },
                  { title: '显示名称', dataIndex: 'display_name', key: 'display_name', render: (value: string) => value || '-' },
                  { title: '状态', dataIndex: 'is_active', key: 'is_active', render: (value: boolean) => (value ? '启用' : '停用') },
                  {
                    title: '操作',
                    key: 'action',
                    render: (_: unknown, record: DeviceRole) => (
                      <Space>
                        <Tooltip title="编辑">
                          <Button type="link" onClick={() => openCatalogEditor(record)}>编辑</Button>
                        </Tooltip>
                        <Popconfirm title="确认删除该设备角色吗？" onConfirm={() => handleDeleteCatalog(record.id)}>
                          <Tooltip title="删除">
                            <Button type="link" danger>删除</Button>
                          </Tooltip>
                        </Popconfirm>
                      </Space>
                    ),
                    hidden: !canModify,
                  },
                ]}
              />
            ),
          },
          {
            key: 'deviceType',
            label: '设备类型',
            children: (
              <Table
                rowKey="id"
                dataSource={deviceTypeCatalogs}
                pagination={false}
                columns={[
                  { title: '名称', dataIndex: 'name', key: 'name' },
                  { title: '显示名称', dataIndex: 'display_name', key: 'display_name', render: (value: string) => value || '-' },
                  { title: '状态', dataIndex: 'is_active', key: 'is_active', render: (value: boolean) => (value ? '启用' : '停用') },
                  {
                    title: '操作',
                    key: 'action',
                    render: (_: unknown, record: DeviceType) => (
                      <Space>
                        <Tooltip title="编辑">
                          <Button type="link" onClick={() => openCatalogEditor(record)}>编辑</Button>
                        </Tooltip>
                        <Popconfirm title="确认删除该设备类型吗？" onConfirm={() => handleDeleteCatalog(record.id)}>
                          <Tooltip title="删除">
                            <Button type="link" danger>删除</Button>
                          </Tooltip>
                        </Popconfirm>
                      </Space>
                    ),
                    hidden: !canModify,
                  },
                ]}
              />
            ),
          },
          {
            key: 'deviceVendor',
            label: '设备厂商',
            children: (
              <Table
                rowKey="id"
                dataSource={deviceVendorCatalogs}
                pagination={false}
                columns={[
                  { title: '名称', dataIndex: 'name', key: 'name' },
                  { title: '显示名称', dataIndex: 'display_name', key: 'display_name', render: (value: string) => value || '-' },
                  { title: '状态', dataIndex: 'is_active', key: 'is_active', render: (value: boolean) => (value ? '启用' : '停用') },
                  {
                    title: '操作',
                    key: 'action',
                    render: (_: unknown, record: DeviceVendor) => (
                      <Space>
                        <Tooltip title="编辑">
                          <Button type="link" onClick={() => openCatalogEditor(record)}>编辑</Button>
                        </Tooltip>
                        <Popconfirm title="确认删除该设备厂商吗？" onConfirm={() => handleDeleteCatalog(record.id)}>
                          <Tooltip title="删除">
                            <Button type="link" danger>删除</Button>
                          </Tooltip>
                        </Popconfirm>
                      </Space>
                    ),
                    hidden: !canModify,
                  },
                ]}
              />
            ),
          },
        ]}
        tabBarExtraContent={canModify ? (
          <Tooltip title={`新增${getCatalogMeta().title}`}>
            <Button type="primary" onClick={() => openCatalogEditor()}>
              新增{getCatalogMeta().title}
            </Button>
          </Tooltip>
        ) : null}
      />

      <Modal
        title={`${catalogEditingItem ? '编辑' : '新增'}${getCatalogMeta().title}`}
        open={catalogEditorOpen}
        onCancel={() => setCatalogEditorOpen(false)}
        onOk={handleSaveCatalog}
        confirmLoading={catalogSaving}
        destroyOnClose
      >
        <Form form={catalogForm} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input />
          </Form.Item>
          <Form.Item name="display_name" label="显示名称">
            <Input />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={3} />
          </Form.Item>
          <Form.Item name="is_active" label="启用状态" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  )
}

export default DeviceDictionaryManager
