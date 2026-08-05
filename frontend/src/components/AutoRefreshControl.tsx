import { InfoCircleOutlined, ReloadOutlined } from '@ant-design/icons'
import { Button, Select, Space, Switch, Tooltip, Typography } from 'antd'
import { useEffect, useRef, useState } from 'react'

const { Text } = Typography

const REFRESH_OPTIONS = [
  { value: 10, label: '10s' },
  { value: 30, label: '30s' },
  { value: 60, label: '60s' },
]

type AutoRefreshControlProps = {
  onRefresh: () => void | Promise<void>
  onManualRefresh?: () => void | Promise<void>
  onConfigChange?: (enabled: boolean, seconds: number) => void
  disabled?: boolean
  disabledTip?: string
  tip?: string
}

const AutoRefreshControl = ({
  onRefresh,
  onManualRefresh,
  onConfigChange,
  disabled = false,
  disabledTip = '当前时间范围不支持自动刷新',
  tip = '本图独立刷新，不影响其他图表；页面位于后台或上一次刷新未结束时会跳过本轮。',
}: AutoRefreshControlProps) => {
  const [enabled, setEnabled] = useState(true)
  const [seconds, setSeconds] = useState(10)
  const [countdown, setCountdown] = useState(10)
  const [refreshing, setRefreshing] = useState(false)
  const runningRef = useRef(false)
  const refreshRef = useRef(onRefresh)

  useEffect(() => {
    refreshRef.current = onRefresh
  }, [onRefresh])

  const executeRefresh = async () => {
    if (runningRef.current || document.visibilityState !== 'visible') return
    runningRef.current = true
    setRefreshing(true)
    try {
      await refreshRef.current()
    } finally {
      runningRef.current = false
      setRefreshing(false)
      setCountdown(seconds)
    }
  }

  useEffect(() => {
    if (!enabled || disabled) return undefined
    const timer = window.setInterval(() => {
      setCountdown((current) => {
        if (current > 1) return current - 1
        if (!runningRef.current && document.visibilityState === 'visible') void executeRefresh()
        return seconds
      })
    }, 1000)
    return () => window.clearInterval(timer)
  }, [disabled, enabled, seconds])

  useEffect(() => {
    setEnabled(!disabled)
    setCountdown(seconds)
    onConfigChange?.(!disabled, seconds)
  }, [disabled])

  const changeEnabled = (checked: boolean) => {
    setEnabled(checked)
    setCountdown(seconds)
    onConfigChange?.(checked, seconds)
  }

  const changeSeconds = (value: number) => {
    setSeconds(value)
    setCountdown(value)
    onConfigChange?.(enabled, value)
  }

  return (
    <Space.Compact size="small">
      <Tooltip title={disabled ? disabledTip : (enabled ? '关闭本图自动刷新' : '开启本图自动刷新')}>
        <Switch
          size="small"
          checked={enabled && !disabled}
          disabled={disabled}
          checkedChildren="自动"
          unCheckedChildren="自动"
          onChange={changeEnabled}
          style={{ minWidth: 48, alignSelf: 'center', marginInlineEnd: 6 }}
        />
      </Tooltip>
      <Select
        size="small"
        value={seconds}
        options={REFRESH_OPTIONS}
        disabled={disabled}
        onChange={changeSeconds}
        style={{ width: 66 }}
      />
      <div style={{ minWidth: 68, padding: '0 7px', display: 'flex', alignItems: 'center', justifyContent: 'center', border: '1px solid #d9d9d9', borderInlineStart: 0, background: '#fff' }}>
        <Text type="secondary" style={{ fontSize: 12 }}>{enabled && !disabled ? `${countdown}s 后刷新` : '已关闭'}</Text>
      </div>
      <Tooltip title="立即刷新本图">
        <Button
          icon={<ReloadOutlined />}
          loading={refreshing}
          disabled={disabled}
          onClick={() => { void (onManualRefresh ? onManualRefresh() : executeRefresh()) }}
        >
          刷新
        </Button>
      </Tooltip>
      <Tooltip title={tip}>
        <Button icon={<InfoCircleOutlined />} style={{ color: '#8c8c8c' }} />
      </Tooltip>
    </Space.Compact>
  )
}

export default AutoRefreshControl
