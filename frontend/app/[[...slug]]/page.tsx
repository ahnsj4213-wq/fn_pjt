import { Screens } from './client';

/** Every path renders the same shell; react-router picks the screen in the browser. */
export default function Page() {
  return <Screens />;
}
