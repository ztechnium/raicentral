import {
  getAISystems,
  getAgentVersions,
  getAgents,
  getRaiRulesUnified,
  getRuntimeIdentities,
} from "@/lib/api";

export async function hasCompleteOnboardingInventory(): Promise<boolean> {
  const [systems, runtimes, rules] = await Promise.all([
    getAISystems(),
    getRuntimeIdentities(),
    getRaiRulesUnified({ limit: 1 }),
  ]);
  if (!systems.data.length || !runtimes.data.length || rules.data.total === 0) return false;

  const agents = (
    await Promise.all(systems.data.map((system) => getAgents(system.id)))
  ).flatMap((response) => response.data);
  if (!agents.length) return false;

  const versions = await Promise.all(agents.map((agent) => getAgentVersions(agent.id)));
  return versions.some((response) => response.data.length > 0);
}
