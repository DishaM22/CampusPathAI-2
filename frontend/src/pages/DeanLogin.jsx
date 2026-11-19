import ReviewerAuthForm from "../features/reviewerAuth/ReviewerAuthForm";
import { DEAN } from "../features/reviewerAuth/roleConfig";

export default function DeanLogin() {
  return <ReviewerAuthForm cfg={DEAN} mode="login" />;
}
