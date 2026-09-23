import { request } from './client'

export type ConfigurationField = { title?: string; type?: string; default?: unknown; enum?: unknown[]; anyOf?: ConfigurationField[]; widget?: string; minimum?: number; maximum?: number; maxLength?: number; description?: string }
export type WorldTypeDescriptor = { id: string; version: number; name: string; description: string; frontend_entry: string;
  configuration_schema: { properties?: Record<string, ConfigurationField>; required?: string[] }; capabilities: Record<string, boolean> }
export type WorldTypeView = { world_name: string; world_type: string; type_version: number; descriptor: WorldTypeDescriptor; revision: number;
  configuration: Record<string, unknown>; initialization: { status: string; resources: Record<string, unknown>; required_fields: string[]; error_code: string | null };
  overview: Record<string, unknown> | null }
export const worldTypes = {
  list: (signal?: AbortSignal) => request<{ items: WorldTypeDescriptor[] }>('/api/world-types', { signal, cache: 'no-store' }),
  current: (signal?: AbortSignal) => request<WorldTypeView>('/api/world-type', { signal, cache: 'no-store' }),
  save: (configuration: Record<string, unknown>, revision: number) => request<WorldTypeView>('/api/world-type/config', {
    method: 'PUT', body: JSON.stringify({ configuration, expected_revision: revision }),
  }),
  initialize: (revision: number) => request<WorldTypeView>('/api/world-type/initialize', { method: 'POST', body: JSON.stringify({ expected_revision: revision }) }),
}
