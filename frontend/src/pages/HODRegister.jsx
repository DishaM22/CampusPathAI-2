import ReviewerAuthForm from "../features/reviewerAuth/ReviewerAuthForm";
import { HOD } from "../features/reviewerAuth/roleConfig";

export default function HODRegister() {
  return <ReviewerAuthForm cfg={HOD} mode="register" />;
}
