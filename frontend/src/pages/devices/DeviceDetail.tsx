import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Card, Descriptions, Tag, Button, Space, Spin } from 'antd'
import { ArrowLeftOutlined, EditOutlined } from '@ant-design/icons'
import { getDevice } from '../../api/devices'
import type { Device } from '../../api/devices'
import { useAuthStore } from '../../store/auth'

const statusMap: Record<string, { color: string; label: string }> = {
  active: { color: 'success', label: '上线' },
  inactive: { color: 'default', label: '离线' },
  in_stock: { color: 'default', label: '库存' },
  deployed: { color: 'processing', label: '上架' },
  online: { color: 'success', label: '上线' },
  offline: { color: 'default', label: '离线' },
}

const isAsterNOSVendor = (vendor?: string) => {
  const value = (vendor || '').toLowerCase()
  return value.includes('asternos') || value.includes('asterfusion') || value.includes('asteros') || value.includes('星融元')
}

const DeviceDetail = () => {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const canModify = !useAuthStore((state) => state.user?.read_only)
  const [device, setDevice] = useState<Device | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetchDevice()
  }, [id])

  const fetchDevice = async () => {
    try {
      const data = await getDevice(Number(id))
      setDevice(data)
    } catch (error) {
      console.error('获取设备失败:', error)
    } finally {
      setLoading(false)
    }
  }

  if (loading) {
    return <Spin style={{ display: 'block', margin: '100px auto' }} />
  }

  if (!device) {
    return <div>设备不存在</div>
  }

  const statusConfig = statusMap[device.status] || { color: 'default', label: device.status }
  const monitorSourceLabel = isAsterNOSVendor(device.vendor)
    ? 'AsterNOS Exporter 直连'
    : device.gnmi?.enabled
      ? 'SNMP 和 Telemetry'
      : 'SNMP'

  return (
    <Card
      title={
        <Space>
          <Button
            type="text"
            icon={<ArrowLeftOutlined />}
            onClick={() => navigate('/devices')}
          />
          <span>{device.name}</span>
          <Tag color={statusConfig.color}>{statusConfig.label}</Tag>
        </Space>
      }
      extra={canModify ? (
        <Button
          type="primary"
          icon={<EditOutlined />}
          onClick={() => navigate(`/devices/edit/${device.id}`)}
        >
          编辑
        </Button>
      ) : null}
    >
      <Descriptions bordered column={2}>
        <Descriptions.Item label="设备名称">{device.name}</Descriptions.Item>
        <Descriptions.Item label="运行状态">{statusConfig.label}</Descriptions.Item>
        <Descriptions.Item label="IP地址">{device.ip_address}</Descriptions.Item>
        <Descriptions.Item label="加入监控">{device.is_monitored ? '是' : '否'}</Descriptions.Item>
        <Descriptions.Item label="所属机房">
          {device.datacenter
            ? `${device.datacenter.name}${device.datacenter.code ? ` (${device.datacenter.code})` : ''}`
            : '-'}
        </Descriptions.Item>
        <Descriptions.Item label="监控方式">
          {device.is_monitored ? monitorSourceLabel : '-'}
        </Descriptions.Item>
        <Descriptions.Item label="设备类型">{device.device_type || '-'}</Descriptions.Item>
        <Descriptions.Item label="设备角色">{device.device_role || '-'}</Descriptions.Item>
        <Descriptions.Item label="厂商">{device.vendor || '-'}</Descriptions.Item>
        <Descriptions.Item label="型号">{device.model || '-'}</Descriptions.Item>
        <Descriptions.Item label="序列号">{device.serial_number || '-'}</Descriptions.Item>
        <Descriptions.Item label="创建时间">{device.created_at || '-'}</Descriptions.Item>
        <Descriptions.Item label="机房位置">{device.datacenter?.location || '-'}</Descriptions.Item>
        <Descriptions.Item label="负责人">{device.datacenter?.contact_person || '-'}</Descriptions.Item>
        <Descriptions.Item label="负责人电话">{device.datacenter?.contact_phone || '-'}</Descriptions.Item>
        <Descriptions.Item label="负责人邮箱">{device.datacenter?.contact_email || '-'}</Descriptions.Item>
        <Descriptions.Item label="建设时间">
          {device.datacenter?.build_date ? new Date(device.datacenter.build_date).toLocaleDateString() : '-'}
        </Descriptions.Item>
      </Descriptions>
    </Card>
  )
}

export default DeviceDetail
