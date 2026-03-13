import { ICON_MAP } from "./iconMap";

type AgentIconProps = {
  iconName: string;
  size?: number;
  style?: React.CSSProperties;
  className?: string;
};

export function AgentIcon({ iconName, size = 16, style, className }: AgentIconProps) {
  const Icon = ICON_MAP[iconName];
  if (!Icon) return null;
  return <Icon size={size} style={style} className={className} />;
}
