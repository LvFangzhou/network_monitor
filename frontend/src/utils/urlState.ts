export const readUrlNumber = (
  params: URLSearchParams,
  key: string,
  fallback?: number,
): number | undefined => {
  const raw = params.get(key)
  if (raw === null || raw.trim() === '') return fallback
  const value = Number(raw)
  return Number.isFinite(value) ? value : fallback
}

export const setUrlValue = (
  params: URLSearchParams,
  key: string,
  value: string | number | boolean | null | undefined,
  defaultValue?: string | number | boolean,
) => {
  if (value === undefined || value === null || value === '' || value === defaultValue) {
    params.delete(key)
    return
  }
  params.set(key, String(value))
}

export const replaceUrlValues = (
  current: URLSearchParams,
  values: Record<string, string | number | boolean | null | undefined>,
  defaults: Record<string, string | number | boolean> = {},
) => {
  const next = new URLSearchParams(current)
  Object.entries(values).forEach(([key, value]) => setUrlValue(next, key, value, defaults[key]))
  return next
}
