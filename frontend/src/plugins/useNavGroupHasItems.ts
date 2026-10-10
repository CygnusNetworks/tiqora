import { useAuth } from "@/auth/AuthContext";
import { usePluginContributions } from "./PluginProvider";

/** True when any plugin contributes a nav entry the user would see in `group`. */
export function useNavGroupHasItems(group: string): boolean {
  const { user } = useAuth();
  return usePluginContributions("agent.nav").some(
    ({ contribution }) => contribution.group === group && (!contribution.admin_only || user?.is_admin),
  );
}
