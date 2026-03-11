import { useEffect, useRef, useState } from "react";
import { Animated, StyleSheet, useWindowDimensions } from "react-native";

// Background images for the slideshow
const IMAGES = [
  require("../../assets/branding/bg.jpg"),
  require("../../assets/branding/bg2.jpg"),
  require("../../assets/branding/bg3.jpg"),
];

const CYCLE_INTERVAL = 15_000; // 15 seconds per image
const FADE_DURATION = 2_000; // 2 second crossfade
const IMAGE_OPACITY = 0.09;

/**
 * Full-screen background slideshow that cycles through branding images
 * with a gentle crossfade at low opacity, matching the web layout.
 */
export function BackgroundSlideshow() {
  const { width, height } = useWindowDimensions();
  const [currentIndex, setCurrentIndex] = useState(0);
  const fadeAnim = useRef(new Animated.Value(1)).current;
  const nextFadeAnim = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    const interval = setInterval(() => {
      const nextIndex = (currentIndex + 1) % IMAGES.length;

      // Crossfade: fade in next, fade out current
      nextFadeAnim.setValue(0);
      Animated.parallel([
        Animated.timing(fadeAnim, {
          toValue: 0,
          duration: FADE_DURATION,
          useNativeDriver: true,
        }),
        Animated.timing(nextFadeAnim, {
          toValue: 1,
          duration: FADE_DURATION,
          useNativeDriver: true,
        }),
      ]).start(() => {
        setCurrentIndex(nextIndex);
        fadeAnim.setValue(1);
        nextFadeAnim.setValue(0);
      });
    }, CYCLE_INTERVAL);

    return () => clearInterval(interval);
  }, [currentIndex, fadeAnim, nextFadeAnim]);

  const nextIndex = (currentIndex + 1) % IMAGES.length;

  return (
    <>
      <Animated.Image
        source={IMAGES[currentIndex]}
        style={[
          styles.image,
          { width, height, opacity: fadeAnim.interpolate({
            inputRange: [0, 1],
            outputRange: [0, IMAGE_OPACITY],
          }) },
        ]}
        resizeMode="cover"
      />
      <Animated.Image
        source={IMAGES[nextIndex]}
        style={[
          styles.image,
          { width, height, opacity: nextFadeAnim.interpolate({
            inputRange: [0, 1],
            outputRange: [0, IMAGE_OPACITY],
          }) },
        ]}
        resizeMode="cover"
      />
    </>
  );
}

const styles = StyleSheet.create({
  image: {
    position: "absolute",
    top: 0,
    left: 0,
  },
});
