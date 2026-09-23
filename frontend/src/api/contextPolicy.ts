import { request } from './client'

export type ContextPolicy = {
  enabled: boolean; trigger_tokens: number | null; reserve_tokens: number; target_tokens: number | null;
  summary_tokens: number; keep_recent: number; model_role_id: number | null; instructions: string;
  cooldown_seconds: number; min_new_tokens: number
}
export type PolicyView = {
  revision: number; policy: ContextPolicy; inherited?: boolean; world_revision?: number;
  effective?: ContextPolicy; notices?: string[];
  limits?: { ceiling_tokens: number; recommended_reserve_tokens: number; recommended_trigger_tokens: number;
    roles: { role_id: number; name: string; window_tokens: number }[] }
}
const path = (cid?: number) => cid === undefined ? '/api/context-policy' : `/api/conversations/${cid}/context/policy`
export const contextPolicy = {
  read: (cid?: number, signal?: AbortSignal) => request<PolicyView>(path(cid), { signal }),
  save: (cid: number | undefined, policy: ContextPolicy | null, revision: number) => request<PolicyView>(path(cid), {
    method: 'PUT', body: JSON.stringify({ expected_revision: revision, policy }),
  }),
}
