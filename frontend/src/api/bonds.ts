import axios from 'axios'
import type { BondListResponse, BondQueryParams, RefreshResponse, RefreshStatus } from './types'

const client = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

export async function fetchBonds(params: BondQueryParams = {}): Promise<BondListResponse> {
  const { data } = await client.get<BondListResponse>('/bonds', { params })
  return data
}

export async function triggerRefresh(): Promise<RefreshResponse> {
  const { data } = await client.post<RefreshResponse>('/bonds/refresh')
  return data
}

export async function fetchRefreshStatus(): Promise<RefreshStatus> {
  const { data } = await client.get<RefreshStatus>('/bonds/refresh/status')
  return data
}
