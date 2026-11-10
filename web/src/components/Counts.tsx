import { num } from "../format";

export function Counts({ tested, cleared }: { tested: number; cleared: number }) {
  return (
    <>
      {num(tested)} series tested, {cleared} flagged by the screen
    </>
  );
}
