import { useState, useEffect } from "react";
import { getApiBase } from "../auth/platform";

interface Features {
  conversationHistory: boolean;
}

const DEFAULT_FEATURES: Features = { conversationHistory: false };

export function useFeatures(): Features {
  const [features, setFeatures] = useState<Features>(DEFAULT_FEATURES);

  useEffect(() => {
    fetch(`${getApiBase()}/health`)
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (data?.features) {
          setFeatures({
            conversationHistory: !!data.features.conversation_history,
          });
        }
      })
      .catch(() => {});
  }, []);

  return features;
}
