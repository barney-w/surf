export type AuthLevel = "public" | "microsoft" | "organisational";

export interface AgentDef {
  id: string;
  label: string;
  description: string;
  authLevel: AuthLevel;
  enabled: boolean;
  accentVar: string;
  iconName: string;
}

export const AGENTS: AgentDef[] = [
  {
    id: "coordinator",
    label: "Coordinator",
    description: "Automatically routes your question to the best specialist agent.",
    authLevel: "public",
    enabled: true,
    accentVar: "--color-agent-coordinator",
    iconName: "AgentCoordinator",
  },
  {
    id: "website_agent",
    label: "Website",
    description: "Search public website content — services, facilities, events and more.",
    authLevel: "public",
    enabled: true,
    accentVar: "--color-agent-general",
    iconName: "AgentFacilities",
  },
  {
    id: "hr_agent",
    label: "HR",
    description: "Policies, leave entitlements, onboarding and workplace procedures.",
    authLevel: "microsoft",
    enabled: true,
    accentVar: "--color-agent-hr",
    iconName: "AgentHr",
  },
  {
    id: "it_agent",
    label: "IT Support",
    description: "VPN, passwords, software, hardware and IT security.",
    authLevel: "organisational",
    enabled: false,
    accentVar: "--color-agent-it",
    iconName: "AgentIt",
  },
  {
    id: "finance_agent",
    label: "Finance",
    description: "Budgets, procurement, expenses and financial policies.",
    authLevel: "organisational",
    enabled: false,
    accentVar: "--color-agent-finance",
    iconName: "AgentFinance",
  },
];

export const AGENT_QUESTIONS: Record<string, string[]> = {
  coordinator: [
    "What leave am I entitled to?",
    "What are the workplace health and safety guidelines?",
    "What services and facilities are available to the community?",
  ],
  website_agent: [
    "What community facilities are available?",
    "What are the opening hours for the library?",
    "How do I register for a community event?",
  ],
  hr_agent: [
    "How much annual leave do I have?",
    "What is the flexible work policy?",
    "How do I submit a leave request?",
  ],
};
