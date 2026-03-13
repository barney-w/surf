import {
  AgentCoordinator,
  AgentFacilities,
  AgentFinance,
  AgentHr,
  AgentIt,
} from "@surf-kit/icons";

export const ICON_MAP: Record<
  string,
  React.ComponentType<{ size?: number; className?: string; style?: React.CSSProperties }>
> = {
  AgentCoordinator,
  AgentFacilities,
  AgentFinance,
  AgentHr,
  AgentIt,
};
