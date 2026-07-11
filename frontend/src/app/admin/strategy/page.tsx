import { redirect } from "next/navigation";

// Strategy 直接进复盘列表（不再有中间的 Strategy Console 菜单）。
export default function StrategyPage() {
  redirect("/admin/strategy/reviews");
}
