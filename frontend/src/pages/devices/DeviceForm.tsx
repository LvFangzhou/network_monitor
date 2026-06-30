import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Form, Input, Select, Button, Card, message, Space, Divider, InputNumber, Switch, Alert } from 'antd'
import { SaveOutlined, ArrowLeftOutlined } from '@ant-design/icons'
import {
  createDevice,
  updateDevice,
  getDevice,
  getDatacenters,
  getDeviceTypesList,
  getDeviceRolesList,
  getDeviceVendorsList,
} from '../../api/devices'
import type { DeviceCreate, DeviceType, Datacenter, DeviceRole, DeviceVendor } from '../../api/devices'

const { Option } = Select

const statusOptions = [
  { value: 'active', label: '上线' },
  { value: 'inactive', label: '离线' },
  { value: 'in_stock', label: '库存' },
  { value: 'deployed', label: '上架' },
]

const interfaceScopeExamples = '例如：400G1/0/1-400G1/0/64，或 1/0/1-1/0/64，多个范围可用逗号、空格或换行分隔'

const normalizeAsterNOSExporterUrl = (ipAddress?: string, value?: string) => {
  const raw = (value || '').trim()
  if (!raw) {
    return ipAddress ? `http://${ipAddress}:8101` : ''
  }
  return /^https?:\/\//i.test(raw) ? raw.replace(/\/$/, '') : `http://${raw.replace(/\/$/, '')}`
}

const isAsterNOSVendor = (vendor?: string) => {
  const value = (vendor || '').toLowerCase()
  return value.includes('asternos') || value.includes('asterfusion') || value.includes('asteros') || value.includes('星融元')
}

const applyVendorMonitoringDefaults = (form: any, vendor?: string) => {
  if (isAsterNOSVendor(vendor)) {
    form.setFieldsValue({
      monitor_source: 'asternos_exporter',
      prometheus_url: undefined,
      prometheus_job: undefined,
      prometheus_instance: undefined,
    })
    return
  }

  form.setFieldsValue({
    monitor_source: 'snmp',
    network_monitor_mode: form.getFieldValue('network_monitor_mode') || 'snmp',
    prometheus_url: undefined,
    prometheus_job: undefined,
    prometheus_instance: undefined,
    snmp: {
      ...(form.getFieldValue('snmp') || {}),
      version: form.getFieldValue(['snmp', 'version']) || 'v2c',
      port: form.getFieldValue(['snmp', 'port']) || 161,
    },
    gnmi: {
      ...(form.getFieldValue('gnmi') || {}),
      port: form.getFieldValue(['gnmi', 'port']) || 57400,
      skip_verify: form.getFieldValue(['gnmi', 'skip_verify']) ?? true,
    },
  })
}

const DeviceForm = () => {
  const [form] = Form.useForm()
  const navigate = useNavigate()
  const { id } = useParams<{ id: string }>()
  const isEdit = !!id

  const [loading, setLoading] = useState(false)
  const [deviceTypes, setDeviceTypes] = useState<DeviceType[]>([])
  const [deviceRoles, setDeviceRoles] = useState<DeviceRole[]>([])
  const [deviceVendors, setDeviceVendors] = useState<DeviceVendor[]>([])
  const [datacenters, setDatacenters] = useState<Datacenter[]>([])

  useEffect(() => {
    fetchOptions()
    if (isEdit) {
      fetchDevice()
    } else {
      form.setFieldsValue({
        status: 'in_stock',
        is_monitored: false,
        monitor_source: 'snmp',
        network_monitor_mode: 'snmp',
        snmp: {
          version: 'v2c',
          port: 161,
          community: 'para@2026',
        },
        ssh: {
          port: 22,
        },
        interface_scope_mode: 'all',
        interface_scope_include: '',
        interface_scope_exclude: '',
      })
    }
  }, [id])

  const fetchOptions = async () => {
    try {
      const [deviceTypesList, deviceRolesList, deviceVendorsList, datacenterList] = await Promise.all([
        getDeviceTypesList(),
        getDeviceRolesList(),
        getDeviceVendorsList(),
        getDatacenters(),
      ])
      setDeviceTypes(deviceTypesList)
      setDeviceRoles(deviceRolesList)
      setDeviceVendors(deviceVendorsList)
      setDatacenters(datacenterList)
    } catch (error) {
      console.error('获取基础选项失败:', error)
    }
  }

  const fetchDevice = async () => {
    try {
      const device = await getDevice(Number(id))
      const interfaceScope = device.custom_fields?.monitoring?.interface_scope || {}
      form.setFieldsValue({
        name: device.name,
        status: device.status || 'in_stock',
        ip_address: device.ip_address,
        datacenter_id: device.datacenter_id,
        device_type: device.device_type,
        device_type_id: device.device_type_id,
        device_role: device.device_role,
        vendor: device.vendor,
        model: device.model,
        serial_number: device.serial_number,
        is_monitored: device.is_monitored,
        monitor_source: device.monitor_source || 'snmp',
        network_monitor_mode: device.gnmi?.enabled ? 'snmp_telemetry' : 'snmp',
        prometheus_url: device.prometheus_url,
        prometheus_job: device.prometheus_job,
        prometheus_instance: device.prometheus_instance,
        gnmi: {
          enabled: device.gnmi?.enabled || false,
          port: device.gnmi?.port || 57400,
          username: device.gnmi?.username,
          password: device.gnmi?.password,
          tls_enabled: device.gnmi?.tls_enabled || false,
          tls_cert: device.gnmi?.tls_cert,
          skip_verify: device.gnmi?.skip_verify ?? true,
        },
        gnmi_subscriptions_text: device.gnmi?.subscriptions ? JSON.stringify(device.gnmi.subscriptions, null, 2) : '',
        snmp: {
          version: device.snmp?.version || 'v2c',
          port: device.snmp?.port || 161,
          community: device.snmp?.community || 'para@2026',
          username: device.snmp?.username,
          auth_protocol: device.snmp?.auth_protocol,
          auth_password: device.snmp?.auth_password,
          priv_protocol: device.snmp?.priv_protocol,
          priv_password: device.snmp?.priv_password,
          security_level: device.snmp?.security_level,
        },
        ssh: {
          port: device.ssh?.port || 22,
          username: device.ssh?.username,
          password: device.ssh?.password,
          key: device.ssh?.key,
        },
        interface_scope_mode: interfaceScope.mode || 'all',
        interface_scope_include: interfaceScope.include || interfaceScope.include_patterns || '',
        interface_scope_exclude: interfaceScope.exclude || interfaceScope.exclude_patterns || '',
        custom_fields_text: device.custom_fields ? JSON.stringify(device.custom_fields, null, 2) : '',
      })
    } catch (error) {
      message.error('获取设备信息失败')
      navigate('/devices')
    }
  }

  const handleSubmit = async () => {
    try {
      const values = await form.validateFields()
      setLoading(true)

      const matchedType = deviceTypes.find((item) => item.name === values.device_type)
      const monitorSource = isAsterNOSVendor(values.vendor) ? 'asternos_exporter' : 'snmp'
      const parsedCustomFields = values.custom_fields_text ? JSON.parse(values.custom_fields_text) : {}
      const gnmiSubscriptions = values.gnmi_subscriptions_text ? JSON.parse(values.gnmi_subscriptions_text) : []
      const exporterUrl =
        monitorSource === 'asternos_exporter'
          ? normalizeAsterNOSExporterUrl(values.ip_address)
          : undefined
      const interfaceScopeMode = values.interface_scope_mode || 'all'
      parsedCustomFields.monitoring = {
        ...(parsedCustomFields.monitoring || {}),
        interface_scope: {
          mode: interfaceScopeMode,
          include: interfaceScopeMode === 'include' ? (values.interface_scope_include || '') : '',
          exclude: interfaceScopeMode === 'exclude' ? (values.interface_scope_exclude || '') : '',
        },
      }
      if (monitorSource === 'asternos_exporter') {
        parsedCustomFields.monitoring = {
          ...(parsedCustomFields.monitoring || {}),
          exporter_profile: 'asternos',
          exporter_url: exporterUrl,
          exporter_port: 8101,
        }
      }
      const data: DeviceCreate = {
        name: values.name,
        status: values.status,
        ip_address: values.ip_address,
        datacenter_id: values.datacenter_id,
        device_type: values.device_type,
        device_type_id: values.device_type_id || matchedType?.id,
        device_role: values.device_role,
        vendor: values.vendor,
        model: values.model,
        serial_number: values.serial_number,
        is_monitored: Boolean(values.is_monitored),
        monitor_source: monitorSource,
        prometheus_url: exporterUrl,
        prometheus_job: undefined,
        prometheus_instance: undefined,
        snmp: {
          version: values.snmp?.version || 'v2c',
          port: values.snmp?.port || 161,
          community: values.snmp?.community,
          username: values.snmp?.username,
          auth_protocol: values.snmp?.auth_protocol,
          auth_password: values.snmp?.auth_password,
          priv_protocol: values.snmp?.priv_protocol,
          priv_password: values.snmp?.priv_password,
          security_level: values.snmp?.security_level,
        },
        gnmi: {
          enabled: monitorSource === 'snmp' ? values.network_monitor_mode === 'snmp_telemetry' : false,
          port: values.gnmi?.port || 57400,
          username: values.gnmi?.username,
          password: values.gnmi?.password,
          tls_enabled: Boolean(values.gnmi?.tls_enabled),
          tls_cert: values.gnmi?.tls_cert,
          skip_verify: values.gnmi?.skip_verify ?? true,
          subscriptions: Array.isArray(gnmiSubscriptions) ? gnmiSubscriptions : [],
        },
        ssh: {
          port: values.ssh?.port || 22,
          username: values.ssh?.username,
          password: values.ssh?.password,
          key: values.ssh?.key,
        },
        custom_fields: parsedCustomFields,
      }

      if (isEdit) {
        await updateDevice(Number(id), data)
        message.success('更新成功')
      } else {
        await createDevice(data)
        message.success('创建成功')
      }

      navigate('/devices')
    } catch (error: any) {
      console.error('提交失败:', error)
      if (error.errorFields) {
        message.error('请填写所有必填字段')
      } else {
        const detail = error.response?.data?.detail
        message.error(typeof detail === 'string' ? detail : '提交失败')
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <Card
      title={
        <Space>
          <Button
            type="text"
            icon={<ArrowLeftOutlined />}
            onClick={() => navigate('/devices')}
          />
          <span>{isEdit ? '编辑设备' : '添加设备'}</span>
        </Space>
      }
    >
      <Form
        form={form}
        layout="vertical"
        style={{ maxWidth: 640 }}
      >
        <Form.Item
          name="name"
          label="设备名称"
          rules={[{ required: true, message: '请输入设备名称' }]}
        >
          <Input placeholder="例如：核心交换机-01" />
        </Form.Item>

        <Form.Item
          name="status"
          label="运行状态"
          rules={[{ required: true, message: '请选择运行状态' }]}
        >
          <Select placeholder="选择运行状态">
            {statusOptions.map((option) => (
              <Option key={option.value} value={option.value}>
                {option.label}
              </Option>
            ))}
          </Select>
        </Form.Item>

        <Form.Item
          name="ip_address"
          label="IP地址"
          rules={[
            { required: true, message: '请输入IP地址' },
            { pattern: /^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$/, message: '请输入有效的IP地址' },
          ]}
        >
          <Input placeholder="192.168.1.1" />
        </Form.Item>

        <Form.Item
          name="datacenter_id"
          label="所属机房"
        >
          <Select placeholder="选择机房" allowClear showSearch optionFilterProp="label">
            {datacenters.filter((item) => item.is_active).map((item) => (
              <Option
                key={item.id}
                value={item.id}
                label={item.code ? `${item.name} (${item.code})` : item.name}
              >
                {item.code ? `${item.name} (${item.code})` : item.name}
              </Option>
            ))}
          </Select>
        </Form.Item>

        <Form.Item
          name="device_type"
          label="设备类型"
          rules={[{ required: true, message: '请选择设备类型' }]}
        >
          <Select
            placeholder="选择设备类型"
            onChange={(value) => {
              const matchedType = deviceTypes.find((item) => item.name === value)
              form.setFieldValue('device_type_id', matchedType?.id)
            }}
          >
            {deviceTypes.filter((item) => item.is_active).map((item) => (
              <Option key={item.id} value={item.name}>
                {item.display_name || item.name}
              </Option>
            ))}
          </Select>
        </Form.Item>

        <Form.Item name="device_type_id" hidden>
          <Input />
        </Form.Item>

        <Form.Item
          name="device_role"
          label="设备角色"
        >
          <Select placeholder="选择设备角色" allowClear showSearch optionFilterProp="label">
            {deviceRoles.filter((item) => item.is_active).map((item) => (
              <Option key={item.id} value={item.name} label={item.display_name || item.name}>
                {item.display_name || item.name}
              </Option>
            ))}
          </Select>
        </Form.Item>

        <Form.Item
          name="vendor"
          label="厂商"
        >
          <Select
            placeholder="选择厂商"
            allowClear
            showSearch
            optionFilterProp="label"
            onChange={(value) => applyVendorMonitoringDefaults(form, value)}
          >
            {deviceVendors.filter((item) => item.is_active).map((vendor) => (
              <Option key={vendor.id} value={vendor.name} label={vendor.display_name || vendor.name}>
                {vendor.display_name || vendor.name}
              </Option>
            ))}
          </Select>
        </Form.Item>

        <Form.Item
          name="model"
          label="型号"
        >
          <Input placeholder="例如：Catalyst 9300" />
        </Form.Item>

        <Form.Item
          name="serial_number"
          label="序列号"
        >
          <Input placeholder="例如：SN123456789" />
        </Form.Item>

        <Divider orientation="left">监控配置</Divider>

        <Form.Item
          name="is_monitored"
          label="是否加入监控"
          valuePropName="checked"
        >
          <Switch checkedChildren="加入" unCheckedChildren="不加入" />
        </Form.Item>

        <Form.Item
          noStyle
          shouldUpdate={(prev, next) =>
            prev.is_monitored !== next.is_monitored ||
            prev.interface_scope_mode !== next.interface_scope_mode
          }
        >
          {({ getFieldValue }) => {
            if (!getFieldValue('is_monitored')) return null
            const mode = getFieldValue('interface_scope_mode') || 'all'

            return (
              <>
                <Form.Item
                  name="interface_scope_mode"
                  label="端口监控范围"
                  extra="默认监控全部端口；如果某些端口接终端、经常重启，可以只监控核心链路端口。"
                >
                  <Select>
                    <Option value="all">全部端口（默认）</Option>
                    <Option value="include">只监控指定端口</Option>
                    <Option value="exclude">排除指定端口</Option>
                  </Select>
                </Form.Item>

                {mode === 'include' && (
                  <Form.Item
                    name="interface_scope_include"
                    label="只监控这些端口"
                    extra={interfaceScopeExamples}
                    rules={[{ required: true, message: '请输入需要监控的端口或端口范围' }]}
                  >
                    <Input.TextArea rows={3} placeholder={'400G1/0/1-400G1/0/64\n400G1/0/101, 400G1/0/103'} />
                  </Form.Item>
                )}

                {mode === 'exclude' && (
                  <Form.Item
                    name="interface_scope_exclude"
                    label="不监控这些端口"
                    extra={interfaceScopeExamples}
                    rules={[{ required: true, message: '请输入需要排除的端口或端口范围' }]}
                  >
                    <Input.TextArea rows={3} placeholder={'400G1/0/65-400G1/0/128\n1/0/65-1/0/128'} />
                  </Form.Item>
                )}

                <Alert
                  type="info"
                  showIcon
                  style={{ marginBottom: 24 }}
                  message="端口范围只影响接口/光模块类告警"
                  description="设备连通性、CPU、内存、BGP、OSPF 等设备级或协议级告警不受影响。保存后，范围外仍在触发的接口告警会自动恢复。"
                />
              </>
            )
          }}
        </Form.Item>

        <Form.Item
          noStyle
          shouldUpdate={(prev, next) =>
            prev.is_monitored !== next.is_monitored ||
            prev.monitor_source !== next.monitor_source ||
            prev.vendor !== next.vendor ||
            prev.ip_address !== next.ip_address
          }
        >
          {({ getFieldValue }) => {
            const isMonitored = getFieldValue('is_monitored')
            const vendor = getFieldValue('vendor')
            const monitorSource = isAsterNOSVendor(vendor) ? 'asternos_exporter' : 'snmp'
            if (!isMonitored) return null

            return (
              <>
                <Form.Item name="monitor_source" hidden>
                  <Input />
                </Form.Item>

                {monitorSource === 'asternos_exporter' ? (
                  <>
                    <Alert
                      type="info"
                      showIcon
                      message="AsterNOS 直连模式"
                      description={`厂商识别为 AsterNOS/Asterfusion，Network_monitor 会按设备管理 IP 自动读取 ${getFieldValue('ip_address') ? `http://${getFieldValue('ip_address')}:8101/metrics` : 'http://设备管理IP:8101/metrics'}，不需要填写 SNMP Community 或 Telemetry 参数。`}
                    />
                  </>
                ) : (
                  <>
                    <Form.Item
                      label="监控方式"
                      name="network_monitor_mode"
                      rules={[{ required: true, message: '请选择监控方式' }]}
                    >
                      <Select
                        onChange={(value) => {
                          form.setFieldsValue({
                            monitor_source: 'snmp',
                            gnmi: {
                              ...(form.getFieldValue('gnmi') || {}),
                              enabled: value === 'snmp_telemetry',
                            },
                          })
                        }}
                      >
                        <Option value="snmp">SNMP</Option>
                        <Option value="snmp_telemetry">SNMP 和 Telemetry</Option>
                      </Select>
                    </Form.Item>
                    <Alert
                      type="info"
                      showIcon
                      message="SNMP / Telemetry 监控模式"
                      description="厂商识别为传统网络设备，请填写 SNMP Community；选择 SNMP 和 Telemetry 时继续填写 gNMI/Telemetry 参数。"
                    />
                  </>
                )}
              </>
            )
          }}
        </Form.Item>

        <Form.Item
          noStyle
          shouldUpdate={(prev, next) =>
            prev.is_monitored !== next.is_monitored ||
            prev.monitor_source !== next.monitor_source ||
            prev.vendor !== next.vendor
          }
        >
          {({ getFieldValue }) => {
            const isMonitored = getFieldValue('is_monitored')
            const vendor = getFieldValue('vendor')
            if (isMonitored && isAsterNOSVendor(vendor)) {
              return null
            }

            return (
              <>
                <Divider orientation="left">SNMP / Telemetry 配置</Divider>

                <Form.Item
                  name={['snmp', 'version']}
                  label="SNMP版本"
                >
                  <Select>
                    <Option value="v1">v1</Option>
                    <Option value="v2c">v2c</Option>
                    <Option value="v3">v3</Option>
                  </Select>
                </Form.Item>

                <Form.Item
                  name={['snmp', 'port']}
                  label="SNMP端口"
                >
                  <InputNumber min={1} max={65535} style={{ width: '100%' }} />
                </Form.Item>

                <Form.Item
                  noStyle
                  shouldUpdate={(prev, next) => prev.snmp?.version !== next.snmp?.version}
                >
                  {({ getFieldValue }) => {
                    const snmpVersion = getFieldValue(['snmp', 'version'])
                    if (snmpVersion === 'v3') {
                      return (
                        <>
                          <Form.Item
                            name={['snmp', 'username']}
                            label="SNMP用户名"
                            rules={[{ required: true, message: '请输入 SNMPv3 用户名' }]}
                          >
                            <Input placeholder="SNMPv3 用户名" />
                          </Form.Item>

                          <Form.Item
                            name={['snmp', 'security_level']}
                            label="安全级别"
                          >
                            <Select allowClear placeholder="SNMPv3 安全级别">
                              <Option value="noAuthNoPriv">noAuthNoPriv</Option>
                              <Option value="authNoPriv">authNoPriv</Option>
                              <Option value="authPriv">authPriv</Option>
                            </Select>
                          </Form.Item>

                          <Form.Item
                            name={['snmp', 'auth_protocol']}
                            label="认证协议"
                          >
                            <Select allowClear placeholder="SNMPv3 认证协议">
                              <Option value="MD5">MD5</Option>
                              <Option value="SHA">SHA</Option>
                            </Select>
                          </Form.Item>

                          <Form.Item
                            name={['snmp', 'auth_password']}
                            label="认证密码"
                          >
                            <Input.Password visibilityToggle={false} placeholder="SNMPv3 认证密码" />
                          </Form.Item>

                          <Form.Item
                            name={['snmp', 'priv_protocol']}
                            label="加密协议"
                          >
                            <Select allowClear placeholder="SNMPv3 加密协议">
                              <Option value="DES">DES</Option>
                              <Option value="AES">AES</Option>
                            </Select>
                          </Form.Item>

                          <Form.Item
                            name={['snmp', 'priv_password']}
                            label="加密密码"
                          >
                            <Input.Password visibilityToggle={false} placeholder="SNMPv3 加密密码" />
                          </Form.Item>
                        </>
                      )
                    }

                    return (
                      <Form.Item
                        name={['snmp', 'community']}
                        label="Community"
                        rules={[{ required: true, message: '请输入 SNMP Community' }]}
                      >
                        <Input.Password visibilityToggle={false} placeholder="v1/v2c 例如：para@2026" />
                      </Form.Item>
                    )
                  }}
                </Form.Item>

                <Divider orientation="left">SSH / 配置备份账号</Divider>

                <Form.Item
                  name={['ssh', 'port']}
                  label="SSH端口"
                  extra="配置备份会优先使用这里的 SSH 参数登录设备。"
                >
                  <InputNumber min={1} max={65535} style={{ width: '100%' }} placeholder="默认 22" />
                </Form.Item>

                <Form.Item name={['ssh', 'username']} label="SSH用户名">
                  <Input placeholder="例如：admin / backup" autoComplete="off" />
                </Form.Item>

                <Form.Item name={['ssh', 'password']} label="SSH密码">
                  <Input.Password visibilityToggle={false} placeholder="用于配置备份，可留空改用私钥" autoComplete="new-password" />
                </Form.Item>

                <Form.Item
                  name={['ssh', 'key']}
                  label="SSH私钥"
                  extra="可选。仅当设备支持密钥登录时填写，密码和私钥至少配置一种。"
                >
                  <Input.TextArea rows={4} placeholder="-----BEGIN OPENSSH PRIVATE KEY-----" />
                </Form.Item>

                <Form.Item
                  noStyle
                  shouldUpdate={(prev, next) =>
                    prev.network_monitor_mode !== next.network_monitor_mode ||
                    prev.gnmi?.enabled !== next.gnmi?.enabled
                  }
                >
                  {({ getFieldValue }) => {
                    const telemetryEnabled = getFieldValue('network_monitor_mode') === 'snmp_telemetry'
                    if (!telemetryEnabled) {
                      return null
                    }

                    return (
                      <>
                        <Divider orientation="left">Telemetry / gNMI 参数</Divider>

                        <Form.Item
                          name={['gnmi', 'port']}
                          label="Telemetry 端口"
                          rules={[{ required: true, message: '请输入 Telemetry 端口' }]}
                        >
                          <InputNumber min={1} max={65535} style={{ width: '100%' }} placeholder="默认 57400" />
                        </Form.Item>

                        <Form.Item name={['gnmi', 'username']} label="Telemetry 用户名">
                          <Input placeholder="gNMI 用户名" />
                        </Form.Item>

                        <Form.Item name={['gnmi', 'password']} label="Telemetry 密码">
                          <Input.Password visibilityToggle={false} placeholder="gNMI 密码" />
                        </Form.Item>

                        <Form.Item
                          name={['gnmi', 'tls_enabled']}
                          label="启用 TLS"
                          valuePropName="checked"
                        >
                          <Switch checkedChildren="启用" unCheckedChildren="关闭" />
                        </Form.Item>

                        <Form.Item
                          name={['gnmi', 'skip_verify']}
                          label="跳过证书校验"
                          valuePropName="checked"
                        >
                          <Switch checkedChildren="跳过" unCheckedChildren="校验" />
                        </Form.Item>

                        <Form.Item name={['gnmi', 'tls_cert']} label="TLS 证书">
                          <Input.TextArea rows={3} placeholder="可选，填写 PEM 证书内容" />
                        </Form.Item>

                        <Form.Item
                          name="gnmi_subscriptions_text"
                          label="Telemetry 订阅路径(JSON)"
                          extra={'例如 [{"path":"/interfaces/interface/state/counters","mode":"sample","sample_interval":10000000000}]'}
                          rules={[
                            {
                              validator: async (_, value) => {
                                if (!value) return
                                const parsed = JSON.parse(value)
                                if (!Array.isArray(parsed)) {
                                  throw new Error('Telemetry 订阅路径必须是数组 JSON')
                                }
                              },
                            },
                          ]}
                        >
                          <Input.TextArea rows={4} placeholder='例如 [{"path":"/interfaces/interface/state/counters","mode":"sample","sample_interval":10000000000}]' />
                        </Form.Item>
                      </>
                    )
                  }}
                </Form.Item>
              </>
            )
          }}
        </Form.Item>

        <Form.Item
          noStyle
          shouldUpdate={(prev, next) =>
            prev.is_monitored !== next.is_monitored ||
            prev.monitor_source !== next.monitor_source ||
            prev.vendor !== next.vendor
          }
        >
          {({ getFieldValue }) => {
            const vendor = getFieldValue('vendor')
            if (isAsterNOSVendor(vendor)) {
              return (
                <Alert
                  type="success"
                  showIcon
                  style={{ marginBottom: 24 }}
                  message="AsterNOS 指标已内置适配"
                  description="CPU、内存、系统状态、接口状态、端口流量、错包、丢包、光功率、BGP/OSPF/CRM 等指标会从 Exporter 文本自动解析，无需填写 JSON 扩展。"
                />
              )
            }

            return (
              <>
                <Divider orientation="left">监控扩展(JSON)</Divider>

                <Form.Item
                  name="custom_fields_text"
                  label="监控扩展配置"
                  extra={'可填写 snmp_private_oids，例如 {"snmp_private_oids":{"bfd_session_state_oid":"1.3.6.x.x","optical_rx_oid":"1.3.6.x.x","optical_tx_oid":"1.3.6.x.x","optical_power_scale":0.1}}'}
                  rules={[
                    {
                      validator: async (_, value) => {
                        if (!value) return
                        JSON.parse(value)
                      },
                    },
                  ]}
                >
                  <Input.TextArea rows={6} placeholder='例如 {"snmp_private_oids":{"bfd_session_state_oid":"1.3.6.1.x.x","optical_rx_oid":"1.3.6.1.x.x","optical_tx_oid":"1.3.6.1.x.x","optical_power_scale":0.1}}' />
                </Form.Item>
              </>
            )
          }}
        </Form.Item>

        <Form.Item>
          <Space>
            <Button onClick={() => navigate('/devices')}>
              取消
            </Button>
            <Button
              type="primary"
              icon={<SaveOutlined />}
              onClick={handleSubmit}
              loading={loading}
            >
              {isEdit ? '保存修改' : '创建设备'}
            </Button>
          </Space>
        </Form.Item>
      </Form>
    </Card>
  )
}

export default DeviceForm
