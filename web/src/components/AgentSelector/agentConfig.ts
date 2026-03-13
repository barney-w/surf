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
    description:
      "Default mode. Surf will automatically route your question to the best agent.",
    authLevel: "public",
    enabled: true,
    accentVar: "--color-agent-coordinator",
    iconName: "Waypoints",
  },
  {
    id: "website_agent",
    label: "Website",
    description:
      "Search public website content — services, facilities, events and more.",
    authLevel: "public",
    enabled: true,
    accentVar: "--color-agent-general",
    iconName: "Wifi",
  },
  {
    id: "hr_agent",
    label: "HR",
    description:
      "Policies, leave entitlements, onboarding and workplace procedures.",
    authLevel: "microsoft",
    enabled: true,
    accentVar: "--color-agent-hr",
    iconName: "BookOpen",
  },
  {
    id: "it_agent",
    label: "IT Support",
    description: "VPN, passwords, software, hardware and IT security.",
    authLevel: "organisational",
    enabled: false,
    accentVar: "--color-agent-it",
    iconName: "HardDrive",
  },
  {
    id: "finance_agent",
    label: "Finance",
    description: "Budgets, procurement, expenses and financial policies.",
    authLevel: "organisational",
    enabled: false,
    accentVar: "--color-agent-finance",
    iconName: "BadgeDollarSign",
  },
];

export const AGENT_MESSAGES: Record<string, string> = {
  coordinator: "I can coordinate specialist agents to answer your questions.",
  website_agent:
    "I can find anything on the website — services, facilities, events and more.",
  hr_agent:
    "Ask me about policies, leave entitlements, onboarding and workplace procedures.",
};

export const AGENT_QUESTIONS: Record<string, string[]> = {
  coordinator: [
    "What can I put in my recycling bin?",
    "How do I book a community centre?",
    "What are the library opening hours?",
  ],
  website_agent: [
    "What community facilities are available?",
    "How do I apply for a parking permit?",
    "Where can I walk my dog off-leash?",
  ],
  hr_agent: [
    "How much notice do I need to give for annual leave?",
    "Do I need a medical certificate for sick leave?",
    "How much annual leave do full-time employees get?",
  ],
};
