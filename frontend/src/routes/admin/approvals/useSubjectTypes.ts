import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { approvalsAdminApi, approvalsAdminKeys } from "@/lib/approvalsAdminApi";

/** Registered subject types and a function giving the translated title of a key (an unknown key stays as it is). */
export function useSubjectTypes() {
  const { t } = useTranslation();
  const q = useQuery({
    queryKey: approvalsAdminKeys.subjectTypes(),
    queryFn: ({ signal }) => approvalsAdminApi.subjectTypes(signal),
    staleTime: 60_000,
  });
  const types = q.data ?? [];
  const title = (key: string) => {
    const known = types.find((s) => s.key === key);
    return known ? t(known.title_key, { defaultValue: key }) : key;
  };
  return { types, title, isError: q.isError };
}
