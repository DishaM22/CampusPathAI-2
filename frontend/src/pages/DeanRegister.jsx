import ReviewerAuthForm from "../features/reviewerAuth/ReviewerAuthForm";
import { DEAN } from "../features/reviewerAuth/roleConfig";

export default function DeanRegister() {
  return <ReviewerAuthForm cfg={DEAN} mode="register" />;
}
