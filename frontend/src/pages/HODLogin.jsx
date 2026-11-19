import ReviewerAuthForm from "../features/reviewerAuth/ReviewerAuthForm";
import { HOD } from "../features/reviewerAuth/roleConfig";

export default function HODLogin() {
  return <ReviewerAuthForm cfg={HOD} mode="login" />;
}
