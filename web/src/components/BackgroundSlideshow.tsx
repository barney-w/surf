import { useEffect, useState } from "react";

const BG_IMAGES = [
  "/branding/bg.jpg",
  "/branding/bg2.jpg",
  "/branding/bg3.jpg",
];

export function BackgroundSlideshow() {
  const [bgIndex, setBgIndex] = useState(0);

  useEffect(() => {
    BG_IMAGES.forEach((src) => {
      const img = new Image();
      img.src = src;
    });
  }, []);

  useEffect(() => {
    const id = setInterval(() => {
      setBgIndex((prev) => (prev + 1) % BG_IMAGES.length);
    }, 15000);
    return () => clearInterval(id);
  }, []);

  return (
    <>
      {BG_IMAGES.map((src, i) => (
        <div
          key={src}
          className="fixed inset-0 bg-cover bg-center bg-no-repeat transition-opacity duration-[2000ms] ease-in-out pointer-events-none"
          style={{
            backgroundImage: `url(${src})`,
            opacity: i === bgIndex ? 0.09 : 0,
          }}
        />
      ))}
    </>
  );
}
